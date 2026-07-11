try:
    from importlib.metadata import PackageNotFoundError, version

    __version__ = version("cli-anything-meerk40t")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0+unknown"
