#!/usr/bin/env bash
# Checks which suites the root Makefile runs, without running any of them. Each case copies the
# Makefile into an empty temporary tree, adds stub experiment folders, and puts stub uv, pnpm and
# xcodebuild first on PATH. Every stub, including the stub folder Makefiles, appends
# "<folder> <command>" to a log, so the log shows which suites ran and with what commands.
# Run it from anywhere: bash tests/makefile.sh
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
# Resolved, because the stubs compare it with $PWD, and macOS's /var is a link to /private/var.
work=$(cd "$(mktemp -d)" && pwd -P)
trap 'rm -rf "$work"' EXIT
export LOG="$work/log" TREE="$work/tree" FAIL_AT=""
failures=0

# A stub fails when FAIL_AT is "<folder> <command>" for it, such as "experiments/evals make".
mkdir "$work/bin"
for tool in uv pnpm xcodebuild; do
	cat >"$work/bin/$tool" <<'EOF'
#!/bin/sh
dir=${PWD#"$TREE"/}
[ "$dir" = "$PWD" ] && dir=.
echo "$dir $(basename "$0") $*" >>"$LOG"
[ "$dir $(basename "$0")" != "$FAIL_AT" ]
EOF
	chmod +x "$work/bin/$tool"
done
export PATH="$work/bin:$PATH"

suites="scoring measure-lab evals recon meter-closeup verification"
folder() {
	case $1 in
	recon | verification) echo "$1" ;;
	*) echo "experiments/$1" ;;
	esac
}

# Makes a tree with the root Makefile and the named experiment suites. Every suite but scoring
# gets a stub Makefile with the target the root calls; scoring gets only its pyproject.toml.
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
		measure-lab | meter-closeup) target=check ;;
		*) target=test ;;
		esac
		printf '%s:\n\t@echo "%s make %s" >>"$$LOG"\n\t@test "$$FAIL_AT" != "%s make"\n' \
			"$target" "$dir" "$target" "$dir" >"$TREE/$dir/Makefile"
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
#    for all 64 combinations. "." is the ios build, which runs from the root.
for mask in $(seq 0 63); do
	present=()
	expected="server web ."
	i=0
	for suite in $suites; do
		if (((mask >> i) & 1)); then
			present+=("$suite")
			expected="$expected $(folder "$suite")"
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
expect_commands measure-lab "experiments/measure-lab make check"
expect_commands evals "experiments/evals uv sync --locked
experiments/evals make test"
expect_commands recon "recon make test"
expect_commands meter-closeup "experiments/meter-closeup make check"
expect_commands verification "verification make test"

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
for FAIL_AT in "experiments/scoring uv" "experiments/measure-lab make" "experiments/evals uv" \
	"experiments/evals make" "recon make" "experiments/meter-closeup make" "verification make"; do
	make_tree $suites
	if run_make check; then
		fail "check passed although $FAIL_AT failed"
	elif [ "$(tail -n 1 "$LOG" | cut -d' ' -f1-2)" != "$FAIL_AT" ]; then
		fail "check kept going after $FAIL_AT failed"
	fi
done
FAIL_AT=""

if [ "$failures" -ne 0 ]; then
	echo "$failures Makefile dispatch case(s) failed" >&2
	exit 1
fi
echo "Makefile dispatch: all cases pass"
