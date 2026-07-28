"""Order execution — the only package that holds a key and sends a signed write.

See ``README.md`` for the module map. The rule that shapes everything here: the pure
modules decide *what* order to send and *whether to send it at all*, and the impure edge
does nothing but transmit. A refusal is never a silent skip — every guard names itself.
"""
