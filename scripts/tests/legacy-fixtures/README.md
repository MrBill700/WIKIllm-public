# Compatibility fixtures

These sanitized script snapshots are test inputs, not supported tools. They intentionally retain old defects so tests can prove the current code prevents regressions and can migrate legacy operational state. They run only against temporary synthetic vaults.

pre-alias predates alias rewriting; in-vault-state supplies the old rollback layout; pre-edit-deny reproduces protected-page corruption; pre-sync-sweep reproduces bytecode residue. legacy_fixture.py maps the tests' historical revision labels to these files. No private Git objects or original commit metadata are included. Missing fixtures fail the test instead of skipping its assertions.

Do not deploy or sync these files. They are project code under the root MIT license. Personal names, paths and tracker examples are generalized consistently with the current scripts.
