import os

# Models here are small; OpenMP thread pools only add contention (a 19x slowdown was measured on a
# busy machine). Must be set before numpy/scikit-learn are imported. Override via the environment.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
