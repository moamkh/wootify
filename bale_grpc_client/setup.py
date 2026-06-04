from setuptools import setup, find_packages

setup(
    name="bale-grpc-client",
    version="0.1.0",
    description="Reverse-engineered Python client for Bale Messenger's gRPC-Web API",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="Research Team",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "httpx>=0.27.0",
        "websockets>=12.0",
        "protobuf>=5.0",
    ],
    extras_require={
        "dev": ["pytest", "pytest-asyncio", "black", "mypy"],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    ],
)
