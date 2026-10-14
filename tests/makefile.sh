#!/usr/bin/env bash
# Checks which suites the root Makefile runs, without running any of them. Each case copies the
# Makefile into an empty temporary tree, adds stub suite folders, and puts stub tools
# first on PATH. Every stub, including the stub folder Makefiles, appends
# "<folder> <command>" to a log, so the log shows which suites ran and with what commands.
# Run it from anywhere: bash tests/makefile.sh
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
# Resolved, because the stubs compare it with $PWD, and macOS's /var is a link to /private/var.
work=$(cd "$(mktemp -d)" && pwd -P)
trap 'rm -rf "$work"' EXIT
export LOG="$work/log" TREE="$work/tree" FAIL_AT="" UNTRACKED=""
failures=0

# FAIL_AT names a folder and tool, or one exact invocation including its arguments.
mkdir "$work/bin"
for tool in uv pnpm xcodebuild git node; do
	cat >"$work/bin/$tool" <<'EOF'
#!/bin/sh
dir=${PWD#"$TREE"/}
[ "$dir" = "$PWD" ] && dir=.
echo "$dir $(basename "$0") $*" >>"$LOG"
command="$dir $(basename "$0")"
[ "$command" != "$FAIL_AT" ] && [ "$command $*" != "$FAIL_AT" ] || exit 1
if [ "$(basename "$0")" = git ] && [ "$1" = ls-files ] && [ -n "$UNTRACKED" ]; then
	printf '%s\n' "$UNTRACKED"
fi
EOF
	chmod +x "$work/bin/$tool"
done
export PATH="$work/bin:$PATH"

suites="scoring measure-lab evals recon meter-closeup integration-viewer"
folder() {
	case $1 in
	recon) echo recon ;;
	integration-viewer) echo tools/integration-viewer ;;
	*) echo "experiments/$1" ;;
	esac
}

stub_target() {
	local dir=$1 target=$2
	printf '%s:\n\t@echo "%s make %s" >>"$$LOG"\n\t@test "$$FAIL_AT" != "%s make"\n\t@test "$$FAIL_AT" != "%s make %s"\n' \
		"$target" "$dir" "$target" "$dir" "$dir" "$target"
}

# Each suite gets its dispatch marker: a stub Makefile, scoring's pyproject.toml,
# or the viewer's package.json. Measure Lab has separate project and check targets.
make_tree() {
	rm -rf "$TREE" "$LOG"
	mkdir -p "$TREE/server" "$TREE/web"
	cp "$root/Makefile" "$TREE/"
	: >"$LOG"
	local suite dir target
	for suite in "$@"; do
		dir=$(folder "$suite")
		mkdir -p "$TREE/$dir"
		case $suite in
		scoring) touch "$TREE/$dir/pyproject.toml"; continue ;;
		integration-viewer) touch "$TREE/$dir/package.json"; continue ;;
		measure-lab | meter-closeup) target=check ;;
		*) target=test ;;
		esac
		stub_target "$dir" "$target" >"$TREE/$dir/Makefile"
		if [ "$suite" = measure-lab ]; then
			stub_target "$dir" project >>"$TREE/$dir/Makefile"
		fi
	done
}

run_make() { make -C "$TREE" "$@" >"$work/out" 2>&1; }

fail() {
	echo "FAIL: $*" >&2
	sed 's/^/  log: /' "$LOG" >&2
	sed 's/^/  make: /' "$work/out" >&2
	failures=$((failures + 1))
}

# 1. `make check` runs server, web and ios, then exactly the experiment suites present, in order,
#    for all 64 combinations. "." is the ios build or project drift check at the root.
for mask in $(seq 0 63); do
	present=()
	expected="server web ."
	i=0
	for suite in $suites; do
		if (((mask >> i) & 1)); then
			present+=("$suite")
			expected="$expected $(folder "$suite")"
			[ "$suite" != measure-lab ] || expected="$expected . $(folder "$suite")"
		fi
		i=$((i + 1))
	done
	make_tree ${present[@]+"${present[@]}"}
	if ! run_make check; then
		fail "check with [${present[*]-}] exited non-zero"
		continue
	fi
	got=$(awk '{print $1}' "$LOG" | uniq | tr '\n' ' ' | sed 's/ $//')
	[ "$got" = "$expected" ] || fail "check with [${present[*]-}] ran [$got], expected [$expected]"
done

# 2. Each suite target runs its CI job's commands, in order.
expect_commands() {
	local suite=$1 expected=$2
	make_tree "$suite"
	if ! run_make "$suite"; then
		fail "make $suite exited non-zero"
		return
	fi
	[ "$(cat "$LOG")" = "$expected" ] || fail "make $suite ran the wrong commands"
}
expect_commands scoring "experiments/scoring uv sync --locked
experiments/scoring uv run ruff check .
experiments/scoring uv run ruff format --check .
experiments/scoring uv run pytest -q"
expect_commands measure-lab "experiments/measure-lab make project
. git diff --exit-code -- experiments/measure-lab/MeasureLab.xcodeproj
. git ls-files --others --exclude-standard -- experiments/measure-lab/MeasureLab.xcodeproj
experiments/measure-lab make check"
expect_commands evals "experiments/evals uv sync --locked
experiments/evals make test"
expect_commands recon "recon make test"
expect_commands meter-closeup "experiments/meter-closeup uv sync --locked
experiments/meter-closeup make check"
expect_commands integration-viewer "tools/integration-viewer node --test test/*.test.js"

# 3. Naming a missing suite fails loudly and runs nothing.
for suite in $suites; do
	make_tree
	if run_make "$suite"; then
		fail "make $suite succeeded without its folder"
	elif [ -s "$LOG" ] || ! grep -q "make $suite: .* is missing" "$work/out"; then
		fail "make $suite without its folder ran commands or gave no reason"
	fi
done

# 4. A failing command inside a present suite fails `make check`, and nothing runs after it.
for FAIL_AT in "experiments/scoring uv" "experiments/measure-lab make" \
	"experiments/measure-lab make project" \
	"experiments/measure-lab make check" \
	". git diff --exit-code -- experiments/measure-lab/MeasureLab.xcodeproj" \
	". git ls-files --others --exclude-standard -- experiments/measure-lab/MeasureLab.xcodeproj" \
	"experiments/evals uv" "experiments/evals make" "recon make" \
	"experiments/meter-closeup uv" "experiments/meter-closeup make" \
	"tools/integration-viewer node"; do
	make_tree $suites
	if run_make check; then
		fail "check passed although $FAIL_AT failed"
	elif [ "$(tail -n 1 "$LOG")" != "$FAIL_AT" ] && [ "$(tail -n 1 "$LOG" | cut -d' ' -f1-2)" != "$FAIL_AT" ]; then
		fail "check kept going after $FAIL_AT failed"
	fi
done
FAIL_AT=""

# 5. New generated project files fail the root check before tests or builds.
make_tree measure-lab
UNTRACKED=experiments/measure-lab/MeasureLab.xcodeproj/new-file
if run_make measure-lab; then
	fail "measure-lab passed with untracked generated files"
elif [ "$(tail -n 1 "$LOG")" != ". git ls-files --others --exclude-standard -- experiments/measure-lab/MeasureLab.xcodeproj" ]; then
	fail "measure-lab kept going after finding untracked generated files"
fi
UNTRACKED=""

if [ "$failures" -ne 0 ]; then
	echo "$failures Makefile dispatch case(s) failed" >&2
	exit 1
fi
echo "Makefile dispatch: all cases pass"
