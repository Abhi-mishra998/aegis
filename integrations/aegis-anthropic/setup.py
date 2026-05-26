from pathlib import Path

from setuptools import find_packages, setup

_here = Path(__file__).parent
_readme = (_here / "README.md").read_text() if (_here / "README.md").exists() else ""

setup(
    name="aegis-anthropic",
    version="1.0.0",
    description="Anthropic tool_use governance middleware for Aegis ACP",
    long_description=_readme,
    long_description_content_type="text/markdown",
    author="Aegis ACP",
    python_requires=">=3.10",
    packages=find_packages(),
    install_requires=["httpx>=0.25"],
    extras_require={"anthropic": ["anthropic>=0.25"]},
    keywords=["ai", "governance", "anthropic", "claude", "security", "aegis"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: Security",
        "Programming Language :: Python :: 3",
    ],
)
