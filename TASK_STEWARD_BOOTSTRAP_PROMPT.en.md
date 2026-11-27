# Task Steward bootstrap prompt

English | [简体中文](TASK_STEWARD_BOOTSTRAP_PROMPT.zh-CN.md)

Complete lightweight P0 for every new project. For manual work, fill in the worksheet's startup section. For a structured project, follow .dual-agent/docs/PROJECT_STARTUP.zh-CN.md. Exercise each automatic check required by the first round with positive and negative cases in the target environment. If a required capability needs adaptation, prepare a separately approved adaptation contract. Before dispatching business work, confirm a ready P0 decision in the worksheet or project_startup.status for structured projects.

Read the current task, project brief, and affected source and tests. Agree on one round worksheet with the user. Specify the goal, implementation identity, exact modification paths, input fingerprints, concrete assertions, commands, external outputs and restrictions. After approval, freeze the inputs, deliver them to the named implementer and read them back from the actual delivery location. Perform acceptance after implementation.

Independently review the submitted changes and affected tests. When reusing evidence, explain why inputs, dependencies, commands and environment still match. Consolidate ordinary failures and state why any check was not run. Close permissions after the decision. Keep one concise summary per round for long-term retention. Preserve materials with active consumers until references are released, and obtain approval for exact deletion targets before cleanup. Select tools according to the contract.
