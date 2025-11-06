# KAITO Model Performance Improvements

## Current Issue
The qwen2.5-coder-7b-instruct model running on KAITO shows hallucination and counting errors:
- Claims "20 pods" but lists only 14 pod names
- Expected answer was 14 pods
- Model has trouble with basic numerical reasoning

## Improvement Strategies

### 1. Enhanced System Prompting
Add counting-specific instructions to the system prompt:

```jinja
# Mathematical and Counting Accuracy
* When counting items (pods, services, nodes, etc.), ALWAYS count carefully and double-check your work
* If you list items, verify the count matches what you claim
* For numerical data, be precise and avoid approximations
* When analyzing kubectl output, count line by line methodically
* Example: If listing pods, count each one: 1. apple-pod, 2. banana-pod, etc.
```

### 2. Tool Output Processing
Modify tool output to include explicit counts:

```python
# In kubectl tools, add count summaries
def kubectl_get_pods():
    result = subprocess.run(...)
    lines = result.stdout.strip().split('\n')[1:]  # Skip header
    count = len(lines)
    return f"Found {count} pods:\n{result.stdout}\n\nTotal count: {count}"
```

### 3. Model Configuration Tuning
Adjust inference parameters for better accuracy:

```python
# In KAITO deployment or Holmes LLM config
temperature = 0.0  # More deterministic
top_p = 0.9        # Reduce hallucination
max_tokens = 2048  # Ensure complete responses
```

### 4. Chain-of-Thought Prompting
Add explicit reasoning steps:

```jinja
* When answering numerical questions, show your work step by step
* Count items methodically: "I see pod 1: apple-pod, pod 2: banana-pod..."
* Verify your final count against the list you provided
```

### 5. Verification Prompts
Add a verification step after counting:

```jinja
* After providing a count, double-check by listing items again
* If your count doesn't match your list, recount carefully
* State your confidence level in numerical answers
```

### 6. Model Alternatives to Test
If improvements aren't sufficient, consider:
- **Larger Qwen model**: qwen2.5-coder-32b-instruct (if available)
- **Different model families**: 
  - mistral-7b-instruct-v0.3
  - llama-3.1-8b-instruct
  - codellama-13b-instruct

### 7. Post-Processing Validation
Add validation logic to catch obvious errors:

```python
def validate_count_response(response, actual_count):
    # Extract claimed count from response
    # Compare with actual kubectl output
    # Flag mismatches for review
```

## Next Steps
1. Implement enhanced prompting first (easiest)
2. Test with temperature=0.0 for more deterministic outputs
3. Add explicit counting instructions to system prompt
4. Consider trying a larger model if available

## Test Command
```bash
./run_kaito_evals.sh 'openai/qwen2.5-coder-7b-instruct' '' 1 '01_how_many_pods'
```