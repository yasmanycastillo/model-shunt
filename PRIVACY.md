# Privacy

Model-Shunt is a local MCP server. It does not intentionally persist prompts,
file contents, model responses, or provider credentials.

## Data sent to providers

`bulk_read` sends the selected text files and the question to the configured
worker provider. `code_write` sends the reference file and generation request
to that provider when it needs model output. `get_available_models` queries
the provider's model endpoint. The provider is selected with
`SHUNT_PROVIDER`/`SHUNT_BASE_URL` and may be local (for example Ollama) or a
remote service. Review that provider's data-retention and training policies
before sending source code or other sensitive information.

## Filesystem scope

By default, file operations are restricted to the server's working directory.
`SHUNT_ALLOWED_ROOTS` can expand this scope and should only contain directories
the operator intends to expose. `code_write` may create or overwrite the
requested target path inside an allowed root.

## Credentials and logs

Provider API keys are read from environment variables at runtime. They are
not included in prompts by Model-Shunt and are not written to its responses.
Do not place credentials in `config.json`, source files, or repository history.
The server does not provide MCP authentication; protect the local process and
any network endpoint used by the configured provider.

For questions or privacy reports, open an issue in the project repository:
https://github.com/yasmanycastillo/model-shunt/issues
