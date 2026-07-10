from app.config import settings

def compute_cost(model: str, prompt_tokens: int, completion_tokens: int):
    default_prices = {"input": 0.0, "output": 0.0}
    prices = default_prices
    
    if hasattr(settings, 'llm_prices') and settings.llm_prices:
        prices = settings.llm_prices.get(model, default_prices)
        
    in_cost = (prompt_tokens / 1000.0) * prices["input"]
    out_cost = (completion_tokens / 1000.0) * prices["output"]
    return in_cost, out_cost, in_cost + out_cost
