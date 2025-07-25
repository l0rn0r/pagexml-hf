from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="pagexml-hf",
    version="0.1.0+fork.1",
    author="Jonas Widmer",
    author_email="jonas.widmer@unibe.ch",
    description="Fork of transkribus-hf (https://github.com/wjbmattingly/transkribus-hf)"
                "Convert Transkribus ZIP files or a PageXML export folder (with images) to HuggingFace datasets",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/l0rn0r/pagexml-hf",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.8",
    install_requires=[
        "datasets>=2.0.0",
        "huggingface_hub>=0.15.0",
        "Pillow>=9.0.0",
        "lxml>=4.6.0",
        "numpy>=1.21.0",
        "tqdm>=4.62.0",
        "chardet>=4.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=6.0",
            "black>=22.0",
            "flake8>=4.0",
            "mypy>=0.910",
        ],
    },
    entry_points={
        "console_scripts": [
            "pagexml-hf=pagexml_hf.cli:main",
        ],
    },
)
