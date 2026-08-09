# Personality types — reference library

Shared knowledge about how to talk to someone, keyed by personality type. This is
**not** user data: nothing here is about any particular person.

## How it is used

1. The user states their type in conversation ("I'm an Enneagram 4").
2. The agent reads the matching file here.
3. The agent writes a personalised version into **that user's profile entries**, using
   `update_my_profile`.
4. From then on it is theirs: always injected, visible to them, and editable by them.
   If it does not fit, they say so and their copy changes — the library stays intact.

Nothing here is injected automatically. A user who never states a type simply never
gets any of it, and the agent must not guess one from how they write.

## Why a copy instead of a lookup

Copying into the profile means the guidance travels with the user's own data, needs no
schema field to point at it, and can diverge from the generic description. A person is
not only their type: the copy is the place where "generic Type 5" becomes "this
particular Type 5".

If the library improves later, the user can ask the agent to refresh their entry.

## Structure of each file

- **Core pattern** — what the type is organised around
- **Drives / Fears / Traps / Growth direction** — the raw material
- **How to talk to them** — praise, pushing, stress, what to avoid, the question worth
  offering
- **Proactively** — what to watch for over days and weeks, and when to reach out first

The last two sections are the point. Descriptions of the nine types are everywhere;
what is rare is a usable answer to "so how do I actually speak to this person".

## Adding other frameworks

The same shape works for Big Five, MBTI or anything else. Name files by framework and
key: `enneagram_3.md`, `big5_high_neuroticism.md`, and so on.

## A limit that applies to every type

Never use what someone fears as leverage to move them. It works in the short term for
every type and corrodes the relationship in all of them. The point of knowing someone's
psychology is to reach them, not to press where it hurts.
