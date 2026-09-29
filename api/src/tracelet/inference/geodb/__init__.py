"""The offline geo databases: catalog, installer, validation and readers (F4.AC5, F10.AC3-4).

The invariant this package exists for (F10.AC4): **a failed or corrupt update never
replaces the version that is serving.** Downloads stream to a staging directory, are
unpacked, validated in a memory-capped subprocess, and only then swapped in by an atomic
symlink rename. Nothing is ever read fully into memory (CLAUDE.md section 5).
"""
