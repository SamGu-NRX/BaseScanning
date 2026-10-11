---
name: technical-writing
description: "Checks and writes technical prose against four standards: Diátaxis for document structure, Google developer style for sentences, STE for instructions, and Global English for syntax. Use for /technical-writing or when writing or reviewing docs, RFCs, READMEs, PR descriptions, or commit messages."
---

# Technical writing

Write so a tired engineer understands it on the first read. Four layers get you there, and each answers one question. What kind of document is this? How do sentences address the reader? How much does each sentence carry? Can any sentence be read two ways? Apply all four.

Three rules sit above the layers:

- **Cut every word that does no work.** If the sentence survives without a word, the word goes. "In order to" is "to". "It is important to note that" is nothing.
- **Use the short, everyday word.** "Use", not "utilize". "Help", not "facilitate". "Do", not "perform". A long word has to earn its length with precision.
- **When a rule makes a sentence worse, fix the sentence another way or leave it alone.** The rules serve the reader. A sentence that follows every rule and still sounds machine-written has failed.

The codebase is the word list. Write the real symbol, file, flag, or command name, not a synonym or a description of it.

Don't invent jargon. Use the words a developer says out loud, such as "move" and "a budget that only decreases" instead of "evacuate" and "ratchet". A named pattern is fine when the doc defines it the first time it appears. To add a word to `unslop`'s abstract-metaphor rule, propose it with its replacement in a pull request.

## Vary the rhythm

The layers decide what a document says and how much each sentence carries. A doc can obey all of them and still read as machine-written, with every sentence clipped short, no view anywhere, and nothing specific.

- Mix sentence lengths on purpose. A short sentence lands a point. A longer one can carry a fact together with its condition or its consequence.
- One thought per sentence does not mean one length per sentence. Split a sentence that carries two thoughts, and keep a long sentence that carries one.
- Take a position where the mode allows it. Explanation weighs trade-offs, so say which one you would pick and why instead of listing pros and cons. Reference stays dry.
- Be specific. "Schema changes can cause issues" tells the reader nothing. "A column rename fails the build" tells them what to watch for.

## Pick the mode first (Diátaxis)

One document, one mode. Two questions pick it. Does the content help the reader act or understand? Does it serve someone learning or someone working?

- Act while learning: **tutorial**.
- Act while working: **how-to**.
- Understand while working: **reference**.
- Understand while learning: **explanation**.

Use these two questions on a whole document or on one sentence, whenever you are unsure what you are writing. Gut feel often picks the wrong mode.

**A tutorial teaches by doing.** You are the teacher, and the learner's success is your job. Open with what the learner will build, not what they will "learn". Every step produces a visible result, early and often. Tell them what they should see, such as the expected output, the prompt change, or the log line. Keep explanation to one clause and a link, because a teaching pause breaks the lesson. Stay concrete, and write as "we", in commands: "First, do x. Now, do y."

**A how-to gets a competent reader to a goal.** It solves a problem a person has, not an operation the machine can perform. Skip teaching and background, and link to them instead. Allow forks and judgment: "If you want x, do y." Name the guide by the task, as in "How to calibrate the radar array", not "Radar array calibration".

**Reference holds facts for lookup.** It describes and does nothing else, with no instructions and no opinions. State facts, options, limits, and errors plainly, without hedging, and leave nothing out. Mirror the structure of the thing described, so a reader can move between the code and the docs. Put material where readers expect it. Generate reference from code where you can, so it stays true.

**Explanation builds understanding of why.** It covers one bounded topic and makes sense away from the product. Each title should still read well with "About" in front of it. Anchor on a real why question, and give the context behind it: design decisions, history, constraints, and alternatives. Explanation is the only mode that allows opinion.

Keep modes apart. A tutorial has no reference tables, reference has no hand-holding, and a how-to has no argument. Split the content into separate documents and link them.

Source: diataxis.fr, fetched 2026-07-18.

## Write sentences to the reader (Google developer style)

- Address the reader as "you", in the present tense. Use "will" only for things that happen later.
- Say who does what: "the compiler checks", not "is checked". Passive is fine only when the actor is unknown or beside the point.
- Write instructions as commands: "Click Submit." State facts plainly. Never write "should be done".
- Put the condition before the instruction: "To delete the document, click Delete." The reader then skips what doesn't apply.
- Put the common case first and exceptions after it.
- Sound like a friend who knows the system. Leave out buzzwords, figures of speech, and "please" in instructions. Never write "simply", "easy", or "quickly" in a procedure. If it were simple, the reader would not be here.
- Don't announce future plans ("we will soon support..."), and don't start consecutive sentences with the same phrase.
- Read an awkward sentence aloud. If it stays awkward, rewrite it.
- Link text says where the link goes, using the page title or a short description. Never write "click here". A sentence of context on the page beats a link off it.
- A heading states the point, not just the topic: "Pick the mode first", not "Modes". Use sentence case. A task heading is a bare verb phrase ("Create an instance"). A concept heading is a noun phrase. Use one h1 per page and skip no levels.
- Use numbered lists for sequences and bullets for everything else. Introduce a list with a complete sentence, and keep its items parallel.
- Put code in code font and UI elements in bold. Use serial commas. Drop "etc.", and say up front when a list is partial.

Source: developers.google.com/style, fetched 2026-07-18.

## Make statements load one at a time (STE rules)

- Give one instruction per sentence, and one thought per sentence everywhere else.
- Split an instruction longer than about 20 words, and any other sentence longer than about 25.
- Put the warning or condition before the step it guards: "If hot oil touches your skin, injuries can occur."
- Keep "the" and "a". "Remove backup file" reads two ways. "Remove the backup file" reads one way.
- Give each word one meaning, and keep it. If "check" means inspect, don't also use it to mean restrain.
- Pick one word per action and stick to it. Write "start" every time, not "start" here and "initiate" there.
- Write procedures as direct commands, never as narration and never in the passive: "Install the component", not "the component must be installed".
- Avoid "-ing" words where you can. They can do several grammatical jobs, so they invite misreadings.

Source: asd-ste100.org (Issue 9, 2025), fetched 2026-07-18. The numbered rules and the dictionary are in the spec PDF. The principles above are the parts that carry over to software docs.

## Leave no sentence open to two readings (Global English)

- Keep words like "only" and "not" next to the word they change. "Only fails on growth" and "fails only on growth" say different things.
- Break up long noun strings. "The proto import budget check script" becomes "the script that checks the proto-import budget".
- Make every "it", "they", and "this" point at one obvious thing. Repeat the noun when in doubt. Never use "this" or "which" to point at a whole clause.
- Don't drop verbs. "Phase 1 moves the converters and Phase 2 the runtime" leaves Phase 2 without one, so give it one.
- Keep the small words that show structure. "Ensure that the switch is off" keeps "that" because it makes the sentence parse one way. Never trade clarity for word count.
- Repeat the article in a series when two things could read as one: "the client and the host", not "the client and host".
- When a sentence can group two ways, show which parts "and" or "or" joins. "Both...and", "either...or", and "if...then" cost nothing and settle it.
- Use periods, not semicolons. Replace an em dash with a new sentence.
- Text in parentheses must be a full grammatical unit, or its own sentence. Never form plurals with "(s)".
- Don't use slashes. Write "a, b, or both" instead of "a/b" or "and/or".
- Call each thing by one name, everywhere. A doc that calls one thing "the gate", "the ratchet", and "the budget check" teaches three things. Rewording a sentence that didn't change costs readers the same way, so leave unchanged sentences alone.
- Skip idioms, slang, Latin abbreviations, and metaphors. A non-native reader, a translator, and an agent all parse plain sentences best.

Source: Kohl, The Global English Style Guide (SAS Press). Guideline text fetched from the Internet Archive and the SAS sample chapter, 2026-07-18.

## Apply unslop and the repository rules

- Apply the `unslop` skill to every doc this skill touches. `unslop` owns the catalog of slop patterns, such as AI vocabulary, filler, hedging, and formatting tells.
- PR descriptions and commit messages are writing too. Every layer except Diátaxis applies to them.
- Product UI strings are not documentation. Follow the product's copy guidelines for those.
- Write real paths and real symbols. Make every count or file-tree claim true at the commit that lands it, and include the command that regenerates it.

## Worked example

Before:

> Configuration of the proto import ratchet budget script parameters is performed via budget.json. Note that it's important to remember that running with --write, which updates the committed budget to reflect the current count, should only be done when lowering it. If exceeded, CI fails.

After:

> `budget.mjs` reads the committed budget from `budget.json` and counts the files that import protos. If the count exceeds the budget, CI fails. Run `budget.mjs --write` only to lower the budget.

Each fix comes from a layer:

- "Configuration is performed" becomes "`budget.mjs` reads", so someone does something (Google).
- "Ratchet" goes, and the script's real filename names it instead (the jargon rule).
- The five-noun string breaks up into plain clauses (Global English).
- "Note that it's important to remember" is deleted (cut every word that does no work).
- The failure condition moves ahead of the step it explains (STE).
- "Should only be done when lowering" becomes a command with "only" next to what it limits (STE).
- "If exceeded" gets a subject, the count (Global English).

## Review checklist

Run these checks on any prose this skill covers. Item 1 applies only to a set of documents.

1. Is each file one Diátaxis mode, with links where modes meet?
2. Is every instruction a command, with its condition in front?
3. Does any sentence carry two instructions or two thoughts? Split it.
4. Can any word go without losing meaning? Cut it.
5. Is "only" next to the word it changes? Does every "it" point at one thing? Does every clause keep its verb?
6. Does each thing have exactly one name across the docs?
7. Would a developer say these words out loud? Replace invented metaphors and fancy synonyms with the plain word or the real symbol name.
8. Are all symbols, paths, and counts real at this commit, with the commands that regenerate the counts?
