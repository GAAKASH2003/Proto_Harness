---
name: code-review
description: Review recent changes or specific files for correctness, security, and cleanliness
---
# Code Review Guidelines

Review code methodically across four dimensions:

1. **Correctness & Logic**:
   - Are edge cases handled?
   - Are error states and exceptions caught properly?
   - Could anything result in deadlocks, unclosed handles, or race conditions?

2. **Security & Safety**:
   - Are user inputs sanitized?
   - Are secrets or credentials hardcoded?
   - Are file operations guarded against path traversal?

3. **Readability & Maintainability**:
   - Are naming conventions clean and descriptive?
   - Is logic overly complex or deeply nested?

4. **Output Format**:
   - Summarize findings clearly with severity levels: `[BLOCKER]`, `[WARNING]`, `[SUGGESTION]`.
   - Provide concrete replacement code snippets for suggested fixes.
