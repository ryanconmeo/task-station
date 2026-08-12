"""Filesystem plumbing shared by both planes. atomic_write: temp file in the SAME dir + os.replace so a reader never sees a partial file; pid-suffixed temp avoids collisions between concurrent sessions."""
import os


def atomic_write(path, text):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
