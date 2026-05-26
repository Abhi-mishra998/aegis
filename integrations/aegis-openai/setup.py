from pathlib import Path

from setuptools import find_packages, setup

_here = Path(__file__).parent
_readme = (_here / "README.md").read_text() if (_here / "README.md").exists() else ""

setup(
    name="aegis-openai",
    version="1.0.0",
    description="OpenAI tool_calls governance middleware for Aegis ACP",
    long_description=_readme,
    long_description_content_type="text/markdown",
    author="Aegis ACP",
    python_requires=">=3.10",
    packages=find_packages(),
    install_requires=["httpx>=0.25"],
    extras_require={"openai": ["openai>=1.0"]},
    keywords=["ai", "governance", "openai", "security", "aegis"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: Security",
        "Programming Language :: Python :: 3",
    ],
)
