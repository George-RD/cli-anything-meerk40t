"""PyPI setup for cli-anything-meerk40t.

PEP 420 namespace package: `cli_anything/` has NO `__init__.py`; each
sub-package (`meerk40t/`) has its own.
"""

from setuptools import find_namespace_packages, setup

setup(
    name="cli-anything-meerk40t",
    version="1.2.0",
    description="Agent CLI harness for MeerK40t laser cutting software",
    long_description=(
        "cli-anything-meerk40t is a stateful CLI + REPL that wraps the real "
        "MeerK40t kernel for headless, agent-driven laser job preparation. "
        "It exposes project, elements, operations, device, export, session, "
        "and console-passthrough commands with --json output for AI agents.\n\n"
        "The real MeerK40t software is a hard dependency — the CLI drives it "
        "via kernel.console() and exports SVG/G-code through the real backend."
    ),
    long_description_content_type="text/plain",
    author="George-RD",
    license="MIT",
    python_requires=">=3.8",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    install_requires=[
        "click>=8.0",
        "prompt_toolkit>=3.0",
        "meerk40t>=0.9.0",
        # Headless deps that meerk40t's setup.py only puts in extras.
        # Without these, GRBL websocket, DXF, and image features break.
        "pyusb>=1.0.0",
        "pyserial",
        "numpy",
        "Pillow>=7.0.0",
        "ezdxf>=0.14.0",
        "requests>=2.25.0",
        "websocket-client",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-meerk40t=cli_anything.meerk40t.meerk40t_cli:cli",
        ],
        "meerk40t.extension": [
            "cli_anything_bridge=cli_anything.meerk40t.mk_plugin:plugin",
        ],
    },
    package_data={
        "cli_anything.meerk40t": ["skills/*.md", "README.md", "profiles/*.json"],
    },
    include_package_data=True,
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Environment :: Console",
        "Topic :: Multimedia :: Graphics",
        "Topic :: Utilities",
    ],
)