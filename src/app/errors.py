class PipelineError(Exception):
    pass


class TransientError(PipelineError):
    """5xx, timeout, reset -- má smysl opakovat."""


class PermanentError(PipelineError):
    """403/404 (měsíc není publikovaný) -- opakování stáhne totéž."""


class DataQualityError(PipelineError):
    """Kontrakt schématu nebo překročený DQ práh -- opakování spadne stejně."""
