from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..demo import create_demo_workspace
from ..models import Organization, User
from ..security import (
    COOKIE,
    ROLES,
    RateLimiter,
    current_user,
    get_db,
    hash_password,
    make_token,
    require,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
login_limit = RateLimiter(10, 60)
demo_limit = RateLimiter(5, 3600)


class Register(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    organization: str = Field(min_length=1, max_length=120)


class Login(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class Member(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    role: str = Field(pattern="^(viewer|analyst|admin)$")


def user_json(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role,
            "organization": {"id": u.org_id, "name": u.org.name, "is_demo": u.org.is_demo}}


def _session(response: Response, user: User) -> dict:
    response.set_cookie(COOKIE, make_token(user), httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
                        max_age=config.TOKEN_HOURS * 3600, path="/")
    return user_json(user)


@router.post("/register", dependencies=[Depends(login_limit)])
def register(body: Register, response: Response, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists.")
    org = Organization(name=body.organization.strip())
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=email, name=body.name.strip(), password_hash=hash_password(body.password),
                role="admin")
    db.add(user)
    db.commit()
    return _session(response, user)


@router.post("/login", dependencies=[Depends(login_limit)])
def login(body: Login, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Email or password is incorrect.")
    return _session(response, user)


@router.post("/demo", dependencies=[Depends(demo_limit)])
def demo(response: Response, db: Session = Depends(get_db)):
    return _session(response, create_demo_workspace(db))


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_json(user)


@router.get("/members")
def members(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": u.id, "name": u.name, "email": u.email, "role": u.role}
            for u in db.scalars(select(User).where(User.org_id == user.org_id).order_by(User.id))]


@router.post("/members")
def add_member(body: Member, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    if user.org.is_demo:
        raise HTTPException(403, "This workspace cannot add members.")
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "An account with this email already exists.")
    m = User(org_id=user.org_id, email=body.email.lower(), name=body.name.strip(),
             password_hash=hash_password(body.password), role=body.role)
    db.add(m)
    db.commit()
    return {"id": m.id, "name": m.name, "email": m.email, "role": m.role}


@router.patch("/members/{member_id}")
def set_role(member_id: int, role: str, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    m = db.get(User, member_id)
    if not m or m.org_id != user.org_id:
        raise HTTPException(404, "Member not found.")
    if role not in ROLES:
        raise HTTPException(400, "Unknown role.")
    if m.id == user.id:
        raise HTTPException(400, "You cannot change your own role.")
    m.role = role
    db.commit()
    return {"id": m.id, "role": m.role}
