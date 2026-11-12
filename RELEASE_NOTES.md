# BlindPilot 0.56.1

Hermes messages now include the submission identifier required by current Hermes gateways. Sending a message such as "Hello" no longer fails with invalid_params because that identifier was missing. Each worker turn gets its own UUID, and resubmitting that turn keeps the same identifier so Hermes can recognize a retry. JSON-RPC request IDs remain separate. Reported by Brandon; fix based on Codex's diagnosis. This change targets current Hermes gateways; older gateways that reject submission_id must be updated before use.
