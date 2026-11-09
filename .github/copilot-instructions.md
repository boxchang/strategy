\# Freqtrade AI Agent - GitHub Copilot Instructions



You are an AI development assistant working on this Freqtrade trading bot project.



\## Mandatory Project Rules



Before performing any coding, strategy development, debugging, optimization, or backtesting task:



1\. Read `AGENTS.md` completely.

2\. Read `README.md` to understand the project structure.

3\. Read the relevant documents under `docs/` before making changes.

4\. Follow the risk management rules in `docs/RISK\_MANAGEMENT.md`.

5\. Follow the strategy development rules in `docs/STRATEGY\_DEVELOPMENT.md`.

6\. Follow the backtesting and scoring rules in `docs/BACKTEST\_AND\_SCORING.md`.

7\. Follow the version control rules in `docs/VERSION\_CONTROL.md`.



\## Trading Safety



\- Never assume a backtest result guarantees future profitability.

\- Never enable live trading without explicit user authorization.

\- Never increase leverage or position size without explicit authorization.

\- Never introduce DCA without explicit authorization.

\- Never expose API keys, passwords, or private configuration files.

\- Never modify unrelated strategies, Docker services, databases, or configurations.



\## Development Workflow



For every strategy modification:



1\. Inspect the existing strategy and configuration.

2\. Explain the proposed changes before implementation.

3\. Identify potential trading and technical risks.

4\. Implement the smallest necessary changes.

5\. Check syntax and compatibility with the installed Freqtrade version.

6\. Run appropriate tests and backtests when the environment permits.

7\. Report actual results. Never invent test or backtest results.



\## Final Report



After completing a task, report:



\- Files inspected

\- Files modified

\- Changes made

\- Risks identified

\- Tests performed

\- Backtest results, if available

\- Remaining issues



If a required document cannot be accessed, explicitly tell the user instead of assuming its contents.

