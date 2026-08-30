# Shadow Sync, Spaces and Extension Boundary

## Local-first queue

Shadow App stores offline Nexus quick actions with an Android Keystore AES-GCM key. The browser can only
enqueue, list and acknowledge opaque actions while it is displaying the trusted Nexus origin. Nexus replays
them through the normal quick-action endpoint; domain validation, risk policy, idempotency and receipts remain
mandatory. A queue entry is removed only after a successful server response.

## Portable export and sync

`shadow.portable.v1` is the user-readable export of preferences, entity projections, activity metadata,
trust statistics, governed memories and stable context references. It intentionally excludes credentials,
attachments and Proposal fields.

`shadow.sync.v1` is the transport envelope for a future relay. The relay sees space/device identifiers,
sequence and ciphertext only. Clients reject sequence rollback and broken `previous_hash` chains before
decrypting. The current release implements local encrypted queuing and portable export; enabling a remote
relay still requires deployment of an authenticated storage endpoint and device key enrollment.

`shadow.space.v1` defines personal and household membership. Domain facts are not shared merely because a
user joins a space: every domain/entity must opt into that space, and restricted entities remain private by
default.

## Extension SDK

The supported extension SDK is the versioned Shadow Plugin contract, not arbitrary JavaScript injection.
A plugin contributes capabilities, Surfaces, Entities, quick actions, stable resource links and optional App
entries. Platform validates and compiles those declarations; Nexus renders only the compiled projection.

MCP Apps can be adapted as a plugin when their tools have explicit capabilities and their UI resource is
declared as a resource/app Surface. App-only tools stay hidden from the model. Untrusted remote HTML never
receives domain credentials or the native App bridge, and external marketplace installation remains outside
the production Profile.
