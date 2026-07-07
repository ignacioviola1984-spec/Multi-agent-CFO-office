"""payments package - the first GOVERNED WRITE capability. Agents are propose-only;
a deterministic engine validates; a registered human approves (distinct from the
proposer); execution goes through a PaymentRail (SandboxRail for demo/tests, a stub
for a real bank/wallet rail). Default posture stays read-only: nothing executes
without an explicit human approval, and auto-execution is off behind a code flag.
"""
