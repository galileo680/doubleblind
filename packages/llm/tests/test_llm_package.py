import doubleblind_llm


def test_package_exposes_version() -> None:
    assert doubleblind_llm.__version__ == "0.1.0"
