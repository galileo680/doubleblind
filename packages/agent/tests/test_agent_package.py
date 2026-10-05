import doubleblind_agent


def test_package_exposes_version() -> None:
    assert doubleblind_agent.__version__ == "0.1.0"
