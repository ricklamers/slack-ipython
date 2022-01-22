import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="slack-ipython",
    version="0.0.3",
    author="R. Lamers",
    author_email="ricklamers@gmail.com",
    description="A Python Slack bot through IPython",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/ricklamers/slack-ipython",
    project_urls={
        "Bug Tracker": "https://github.com/ricklamers/slack-ipython/issues",
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    package_dir={"": "src"},
    packages=setuptools.find_packages(where="src"),
    install_requires=[
        "ipykernel",
        "matplotlib",
        "slack_bolt",
        "python-dotenv",
        "snakemq",
    ],
    entry_points={"console_scripts": ["slack-ipython=slack_ipython.main:main"]},
    python_requires=">=3.6",
)
