class Bencharness < Formula
  include Language::Python::Virtualenv

  desc "Compare agent harnesses with scoped, recorded benchmark evidence"
  homepage "https://github.com/ogarciarevett/bencharness"
  url "https://github.com/ogarciarevett/bencharness/archive/42897df4a02a546b036c39ce746ffa2268083b12.tar.gz"
  version "0.1.0"
  sha256 "59d62b2fee3cfce798f3b251e9fa48404ee8fca72c193a8da1a0d923920204f3"
  license "Apache-2.0"

  depends_on "python@3.12"

  resource "setuptools" do
    url "https://files.pythonhosted.org/packages/70/b3/c19426f783614d625699a4b247faf628ea9ab1db228674491767b417f9e1/setuptools-84.2.1.tar.gz"
    sha256 "3ff8b3b5492a0aa66960f4419365a86b84e013070d4313e4d4a6894f5d64d6bc"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "bencharness 0.1.0", shell_output("#{bin}/bench --version")
    assert_match "--report", shell_output("#{bin}/bench --help")
  end
end
