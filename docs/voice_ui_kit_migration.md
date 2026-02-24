# Voice UI Kit Migration Notes

This project was upgraded to the latest tested Pipecat backend stack (`pipecat-ai 0.0.103`) while keeping the existing browser UI and websocket transport.

Why the current UI was kept:
- `@pipecat-ai/voice-ui-kit` expects Pipecat client transports (`smallwebrtc` or Daily).
- This app uses a custom websocket protocol plus custom Setup/Advanced workflows:
  - rulebook upload + sync
  - Qdrant knowledge controls
  - memory inspection/clear
  - campaign/player identity management
  - settings + secrets save-before-connect flows

Replacing the UI directly with Voice UI Kit today would remove or break those flows.

## Safe migration plan

1. Add a SmallWebRTC transport path in the backend (keep websocket path during transition).
2. Expose control RPC parity over HTTP endpoints (or dedicated data-channel messages).
3. Build a React client using `@pipecat-ai/voice-ui-kit` for voice/session controls.
4. Recreate Setup/Advanced/Knowledge/Memory panels around the Voice UI Kit shell.
5. Switch default UI only after parity tests pass.

## Current status

- Backend dependencies are updated and compatible.
- Existing UI remains the primary, fully-featured interface.
- Voice UI Kit migration is tracked as an architectural migration, not a drop-in theme change.
