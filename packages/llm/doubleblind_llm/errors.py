class LLMError(Exception):
    pass


class ConfigurationError(LLMError):
    pass


class MissingApiKeyError(LLMError):
    pass


class BudgetExceededError(LLMError):
    def __init__(self, spent_usd: float, estimate_usd: float, limit_usd: float) -> None:
        self.spent_usd = spent_usd
        self.estimate_usd = estimate_usd
        self.limit_usd = limit_usd
        super().__init__(
            f"Spend limit reached: spent ${spent_usd:.4f} + estimated ${estimate_usd:.4f} "
            f"exceeds limit ${limit_usd:.2f}"
        )


class ReplayMissError(LLMError):
    def __init__(self, cache_key: str, role: str, report: str = "") -> None:
        self.cache_key = cache_key
        self.role = role
        self.report = report
        message = f"No recorded response for role '{role}' with cache key {cache_key}"
        super().__init__(f"{message}\n{report}" if report else message)


class StructuredOutputError(LLMError):
    def __init__(self, errors: list[str], content: str | None) -> None:
        self.errors = errors
        self.content = content
        super().__init__("Model output does not match the schema: " + "; ".join(errors))
