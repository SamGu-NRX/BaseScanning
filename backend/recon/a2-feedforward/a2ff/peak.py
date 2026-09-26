"""Run a module's __main__ and report peak RSS and wall time: python -m a2ff.peak a2ff.run ..."""
import resource
import runpy
import sys
import time

mod = sys.argv[1]
sys.argv = [mod] + sys.argv[2:]
t = time.time()
try:
    runpy.run_module(mod, run_name="__main__")
finally:
    print(f"peak RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6:.2f} GB, wall {time.time() - t:.0f}s", flush=True)
