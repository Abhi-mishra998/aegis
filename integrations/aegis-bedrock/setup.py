from pathlib import Path

from setuptools import find_packages, setup

_here = Path(__file__).parent
_readme = (_here / "README.md").read_text() if (_here / "README.md").exists() else ""

setup(
    name="aegis-bedrock",
    version="1.1.5",
    description="AWS Bedrock Agents governance middleware for Aegis ACP (maintenance-only — Anthropic SDK is the active hero)",
    long_description=_readme,
    long_description_content_type="text/markdown",
    author="Aegis ACP",
    license="Apache-2.0",
    url="https://github.com/Abhi-mishra998/aegis",
    project_urls={
        "Homepage":      "https://github.com/Abhi-mishra998/aegis",
        "Documentation": "https://github.com/Abhi-mishra998/aegis/blob/main/integrations/aegis-bedrock/README.md",
        "Repository":    "https://github.com/Abhi-mishra998/aegis",
        "Bug Tracker":   "https://github.com/Abhi-mishra998/aegis/issues",
    },
    python_requires=">=3.10",
    packages=find_packages(),
    install_requires=["httpx>=0.25"],
    extras_require={"bedrock": ["boto3>=1.34"]},
    keywords=["ai", "governance", "bedrock", "aws", "security", "aegis", "guardrails", "tool-use"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Information Technology",
        "Topic :: Security",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    ],
)
