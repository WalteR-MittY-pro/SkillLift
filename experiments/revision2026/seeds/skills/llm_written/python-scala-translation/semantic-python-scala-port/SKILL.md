---
name: semantic-python-scala-port
description: Port a Python API to idiomatic Scala 2.13 while preserving externally tested behavior.
---

# semantic-python-scala-port

## Workflow
1. Read the public Python definitions and the Scala test specification before choosing types. Create an API map covering all required classes, constructors, method names, return types, and error cases.
2. Translate semantics, not syntax: use sealed traits or enumerations for token kinds, immutable case classes for tokens, Option for expected absence, Either/Try for recoverable parsing errors, and standard collections. Preserve observable ordering and metadata copy behavior.
3. Pay attention to Python versus Scala differences in truthiness, Unicode whitespace, numeric parsing, datetime interpretation, null handling, and dictionary ordering. Express each difference as a testable rule.
4. Implement a vertical slice, compile with Scala 2.13, then run the supplied Scala tests unchanged. Add small parity probes from the Python source for cases tests do not cover, without copying implementation details blindly.

## Example
    final case class Token(value: String, kind: TokenType, metadata: Map[String, String] = Map.empty) {
      def withMetadata(pair: (String, String)): Token = copy(metadata = metadata + pair)
    }

Match the actual test signature before adopting an illustrative type such as String metadata.
