# Publish the first package

The release is prepared but PyPI publication requires the owner's existing login.
No account, token or release is created by the install tests.

While signed into [PyPI's pending publisher form](https://pypi.org/manage/account/publishing/),
register project `bencharness`, GitHub owner `ogarciarevett`, repository
`bencharness`, workflow `release.yml`, environment `pypi`. Configure a required
reviewer on the repository's `pypi` environment before publishing.

Then the owner runs one line from a checkout with authenticated GitHub access:

```sh
gh workflow run release.yml --repo ogarciarevett/bencharness --ref main -f version=0.1.0
```

The manual main-only workflow builds a wheel and source distribution, uses
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
without stored API tokens, and attaches distributions and SHA-256 checksums to
the matching GitHub release. The build and publication jobs have separate
permissions. It refuses a version different from `pyproject.toml`.
Download the release files into one folder and run `sha256sum -c SHA256SUMS`
to verify the attached distributions.

Before the first publication, test installation using the locally built wheel;
the package name on PyPI and the default curl installer become available after
the owner completes that step. No publication is claimed by CI installation.
