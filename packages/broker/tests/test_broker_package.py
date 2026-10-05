import doubleblind_broker


def test_package_exposes_version() -> None:
    assert doubleblind_broker.__version__ == "0.1.0"
