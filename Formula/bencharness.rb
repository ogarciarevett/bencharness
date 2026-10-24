class Bencharness < Formula
  include Language::Python::Virtualenv

  desc "Compare agent harnesses with scoped, recorded benchmark evidence"
  homepage "https://github.com/ogarciarevett/bencharness"
  url "https://github.com/ogarciarevett/bencharness/archive/42897df4a02a546b036c39ce746ffa2268083b12.tar.gz"
  version "0.1.0"
  sha256 "59d62b2fee3cfce798f3b251e9fa48404ee8fca72c193a8da1a0d923920204f3"
  license "Apache-2.0"

  depends_on "python@3.15"
  depends_on "python-setuptools" => :build

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "bencharness 0.1.0", shell_output("#{bin}/bench --version")
    assert_match "--report", shell_output("#{bin}/bench --help")
  end
end
