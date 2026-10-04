# Python version check: 3.11-3.13
import sys
import warnings


if not (3, 11) <= sys.version_info[:2] <= (3, 13):
    warnings.warn(
        "Unsupported Python version {ver}, please use 3.11-3.13".format(
            ver=".".join(map(str, sys.version_info[:3]))
        ),
        RuntimeWarning,
        stacklevel=2,
    )
