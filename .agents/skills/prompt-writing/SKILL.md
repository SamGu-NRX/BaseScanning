---
name: prompt-writing
description: Writes and reviews instructions for AI agents, such as AGENTS.md lines, SKILL.md files and prompts that hand work to another agent or subagent. Use when writing or editing AGENTS.md, CLAUDE.md or a skill, or when briefing another agent.
---

# Prompt writing

The reader is a capable model that can read the repository. Write only what it can't work out from the code, and say each thing once.

## Give the outcome, the reason and the finish

- State the outcome, the scope and what done looks like. An agent with a clear done condition doesn't stop early.
- Give the reason behind each constraint. A model that knows why a rule exists applies it to cases the rule didn't name.
- Point at the exact path, symbol, command or README section. An agent follows "read `server/README.md`, section 'What settles each check'" and skims past "see the server docs".
- Say what to do rather than what to avoid.
- Hand over what the agent can't find in the repository: decisions already made and why, what was tried and failed, who owns which files, and what the user prefers.

## Keep instructions calm and consistent

- Write in a normal voice. Current Claude models follow instructions closely, and capitals, "CRITICAL" or "if in doubt, always" make them apply a rule too widely.
- Put each instruction in one place. When a skill, AGENTS.md and the prompt disagree, a model can stop and wait for help. Where instructions can conflict, say which wins. The user's instructions outrank a skill.
- Write the prompt in the form you want back. A prompt in prose tends to get prose back, and one built from headings and bullets tends to get markdown.
- Call each thing by one name throughout.

## Keep context small

- Every line of AGENTS.md or CLAUDE.md loads into every session. If only one task in ten needs some material, move it to its own file and leave a pointer.
- Link every reference file directly from the entry file. An agent may read only part of a file it reached through another reference.
- Leave out facts that expire. If a fact must stay, point to the file that holds its current value.

## Prune by behavior

- A line does nothing if the agent acts the same without it. Delete the whole sentence, not a few of its words.
- Don't ask an agent to "shorten" or "streamline" a prompt. It will cut for length and drop instructions that mattered.
- Test the prompt by running it. Give a fresh agent a real task with only this prompt, watch where it goes wrong, and fix that failure rather than one you imagine.

## Write a skill

- Write the description in the third person. Say what the skill does and when to use it, in the words a user would type. The description is all the agent sees before it picks the skill.
- Keep SKILL.md under 500 lines. Move detail that only one branch of the task needs into a file that SKILL.md links directly.

## Sources

- Anthropic, [Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices): give context and motivation, say what to do instead of what not to do, match prompt style to output, and tone down aggressive language on current models.
- Anthropic, [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): assume the model is capable, write third-person descriptions, keep references one level deep, leave out time-sensitive facts, and test with real tasks.
- OpenAI, [Prompting the latest model](https://developers.openai.com/api/docs/guides/latest-model): conflicting skill guidance can block work early, and the user's instructions should outrank a skill.
- Matt Pocock, [Writing skills for agents](https://aihero.dev/skills-writing-for-agents): pointer wording, done conditions, the no-op test, and testing by running the document.
