import doubleblind_contract


def test_package_exposes_version() -> None:
    assert doubleblind_contract.__version__ == "0.1.0"
