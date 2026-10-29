# Contributing

Use a focused pull request with a clear problem, change summary, and validation evidence. The pull request template in `.github/pull_request_template.md` lists the required sections.


## AI-assisted contributions

AI assistance is welcome. In the PR description, disclose each tool, the model identifier when exposed, and the scope of its help. If the model is not exposed, say `unknown (not exposed)`; do not guess. The last line of the pull request template is where this goes.

Example disclosure (replace the example with accurate details):

```text
AI assistance: tool = coding assistant; model = unknown (not exposed).
Scope: helped draft the change and tests.
Human responsibility: I reviewed and understand every submitted change.
Validation: list actual checks and results, or explain checks not run.
```

A named human must review and understand the complete change, verify its tests and claims, check security and privacy, and confirm the right to contribute all code and dependencies under the applicable licenses. AI assistance does not waive existing review, signing, coverage, or CI requirements. Do not include secrets, personal data, private prompts, conversation transcripts, or hidden reasoning in the disclosure.
