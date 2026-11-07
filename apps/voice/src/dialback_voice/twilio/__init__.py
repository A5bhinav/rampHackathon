"""The Twilio ConversationRelay adapter (spec 0013): the live analog of the text phone line.

`relay.py` runs the same policy.step() over a ConversationRelay WebSocket; `origination.py` builds
the TwiML that starts such a call and verifies Twilio's webhook signatures. Nothing here talks to a
live Twilio account: dialing and the network sit behind seams the tests fake, so the adapter logic
runs with no account, phone number or model key."""
