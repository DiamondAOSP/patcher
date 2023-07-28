#!/usr/bin/env python3

import argparse
import os
import re
import subprocess

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import colors


def repo(*args: str, repo_dir: str) -> int:
    return subprocess.check_call(["repo"] + list(args), cwd=repo_dir)


def repo_output(*args: str, repo_dir: str, check: bool = True) -> str:
    data: str

    try:
        data = subprocess.check_output(["repo"] + list(args), cwd=repo_dir, encoding="UTF-8")
    except subprocess.CalledProcessError as ex:
        if check:
            raise
        else:
            data = ex.output

    return data.rstrip("\n")


def repo_start(repo_dir: str) -> int:
    return repo("start", "diamondaosp", repo_dir=repo_dir)


def git(*args: str, repo_dir: str) -> int:
    return subprocess.check_call(["git"] + list(args), cwd=repo_dir)


def git_output(*args: str, repo_dir: str) -> str:
    return subprocess.check_output(["git"] + list(args), cwd=repo_dir, encoding="UTF-8").rstrip("\n")


def get_upstream_revision(repo_dir: str) -> str:
    upstream_revision = repo_output("forall", repo_dir, "-c", f"echo $REPO_LREV", repo_dir=repo_dir)
    if not upstream_revision:
        raise Error("manifest_revision_id is empty")

    return upstream_revision


def disable_signing(repo_dir: str):
    git("config", "--local", "commit.gpgsign", "false", repo_dir=repo_dir)


top = repo_output("--show-toplevel", repo_dir=os.getcwd())
projects_dir = os.path.join(top, ".repo", "manifests", "patches")


@dataclass
class Project:
    name: str
    dir: str
    patches_dir: str

    @staticmethod
    def ensure_dir_is_valid(project_dir: str) -> bool:
        if not os.path.isdir(project_dir):
            print(f"{colors.RED}Project {colors.CYAN}{project_dir}{colors.RED} doesn't exist{colors.RESET}")
            return False

        if not os.path.isdir(os.path.join(project_dir, ".git")):
            print(f"{colors.RED}Project {colors.CYAN}{project_dir}{colors.RED} isn't a git repo{colors.RESET}")
            return False

        return True


def discover_projects():
    projects = dict[str, Project]()

    for patches_dir, _, files in os.walk(projects_dir):
        if patches_dir == projects_dir:
            continue

        if len(files) <= 0:
            continue

        relative_path = os.path.relpath(patches_dir, projects_dir)
        project_dir = os.path.join(top, relative_path)

        if not Project.ensure_dir_is_valid(project_dir):
            continue

        projects[relative_path] = Project(relative_path, project_dir, patches_dir)

    return projects


def get_target_projects(projects: dict[str, Project], project_names: list[str]):
    target_projects: Iterable[Project]

    if project_names:
        target_projects: list[Project] = []
        for name in project_names:
            project = projects.get(name)
            if project:
                target_projects.append(project)
            else:
                print(f"{colors.RED}Project {colors.CYAN}{name}{colors.RED} not found{colors.RESET}")
    else:
        target_projects = projects.values()

    return target_projects


def init(projects: dict[str, Project], args):
    project_name: str = os.path.relpath(args.project, top)

    project_dir = os.path.join(top, project_name)

    if not Project.ensure_dir_is_valid(project_dir):
        return

    if project_name not in projects:
        patches_dir = os.path.join(projects_dir, project_name)
        os.makedirs(patches_dir, exist_ok=True)
        os.close(os.open(os.path.join(patches_dir, ".keep"), os.O_CREAT))

    disable_signing(project_dir)
    repo_start(project_dir)


@dataclass
class Patch:
    project: Project
    file_name: str
    feature: str | None
    message: str


def update_readme(projects: dict[str, Project]):
    patches_readme = os.path.join(projects_dir, "README.md")
    if os.path.isfile(patches_readme):
        features: dict[str | None, list[Patch]] = dict()

        for relative_path, project in projects.items():
            for patch_file_name in os.listdir(project.patches_dir):
                patch_text = Path(os.path.join(project.patches_dir, patch_file_name)).read_text()

                message = re.search("Subject: \[PATCH\] (.+)$", patch_text, re.MULTILINE).group(1)

                feature_match = re.search(r"Feature: (.+)$", patch_text, re.MULTILINE)
                feature = feature_match.group(1) if feature_match else None

                features.setdefault(feature, []).append(Patch(project, patch_file_name, feature, message))

        with open(patches_readme, "w") as f:
            def write_feature(feature: str, patches: list[Patch]):
                f.write(f"## {feature}\n\n")
                for patch in patches:
                    path = f"./{patch.project.name}/{patch.file_name}"
                    f.write(
                        f"- [`{patch.project.name}` {patch.message}]({path})\n"
                    )
                f.write("\n")

            miscellaneous: list[Patch] | None

            for feature, patches in features.items():
                if feature is not None:
                    write_feature(feature, patches)
                else:
                    miscellaneous = patches

            if miscellaneous is not None:
                write_feature("miscellaneous", miscellaneous)


def rebuild(projects: dict[str, Project], args):
    for project in get_target_projects(projects, args.project):
        print(f"Rebuilding patches for {colors.CYAN}{project.name}{colors.RESET}")

        if os.path.isdir(os.path.join(project.dir, ".git", "rebase-apply")):
            raise NotImplementedError("handle rebases is not implemented")

        upstream_revision = get_upstream_revision(project.dir)
        print(f"  Upstream revision: {colors.CYAN}{upstream_revision}{colors.RESET}")

        patches_dir = project.patches_dir

        for file in os.listdir(patches_dir):
            if file.endswith('.patch') or file == ".keep":
                os.remove(os.path.join(patches_dir, file))

        git("format-patch", "--quiet",
            "--no-stat", "--no-numbered", "--zero-commit", "--full-index", "--no-signature",
            "-o", patches_dir,
            upstream_revision,
            repo_dir=project.dir)

        [print(f"  {colors.CYAN}{file}{colors.RESET}") for file in os.listdir(patches_dir) if file.endswith('.patch')]

    update_readme(projects)


def apply(projects: dict[str, Project], args):
    for i, project in enumerate(get_target_projects(projects, args.project)):
        if i > 0:
            print()

        if git_output("status", "--porcelain=v1", repo_dir=project.dir) != "":
            print(
                f"{colors.RED}There are uncommited changes in {colors.CYAN}{project.name}{colors.RED}, skipping{colors.RESET}")
            continue

        print(f"Applying patches to {colors.CYAN}{project.name}{colors.RESET}")

        if os.path.isdir(os.path.join(project.dir, ".git", "rebase-apply")):
            git("am", "--abort", repo_dir=project.dir)

        disable_signing(project.dir)
        repo_start(project.dir)

        upstream_revision = get_upstream_revision(project.dir)

        reset_output = git_output("reset", "--keep", upstream_revision, repo_dir=project.dir)
        print(f"Reset to {upstream_revision}: " + reset_output)

        patches = [os.path.abspath(os.path.join(project.patches_dir, p)) for p in os.listdir(project.patches_dir)]
        git("am", "--3way", "--ignore-whitespace", *patches, repo_dir=project.dir)

    if not args.project:
        patched_projects = repo_output(
            "forall", "-c",
            "[[ \"$(git rev-parse --abbrev-ref HEAD)\" == \"diamondaosp\" ]] && echo $REPO_PATH",
            repo_dir=top,
            check=False).split("\n")
        no_longer_patched_projects = list(filter(lambda p: not projects.get(p), patched_projects))
        if no_longer_patched_projects:
            print(f"Reverting {colors.CYAN}{' '.join(no_longer_patched_projects)}{colors.RESET}")
            repo("abandon", "--quiet", "diamondaosp", *no_longer_patched_projects, repo_dir=top)


def main():
    projects = discover_projects()

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(required=True)

    rebuild_parser = subparsers.add_parser("init")
    rebuild_parser.add_argument("project", type=str)
    rebuild_parser.set_defaults(func=init)

    rebuild_parser = subparsers.add_parser("rebuild")
    rebuild_parser.add_argument("project", type=str, nargs='*')
    rebuild_parser.set_defaults(func=rebuild)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("project", type=str, nargs='*')
    apply_parser.set_defaults(func=apply)

    args = parser.parse_args()
    args.func(projects, args)


if __name__ == '__main__':
    main()
