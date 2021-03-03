import re
import os
import sys
import json
import time
import argparse
import requests
import logging
import demisto_sdk.commands.common.tools as tools
from Tests.scripts.utils.log_util import install_logging
from demisto_sdk.commands.validate.validate_manager import ValidateManager

# disable insecure warnings
requests.packages.urllib3.disable_warnings()

PRIVATE_BUILD_INFRA_SCRIPTS = ['Tests/scripts/validate_premium_packs.sh', 'Tests/scripts/validate_premium_packs.py',
                               'Tests/scripts/validate_index.py']
PRIVATE_BUILD_INFRA_FOLDERS = ['Tests/private_build', 'Tests/Marketplace']

TRIGGER_BUILD_URL = 'https://api.github.com/repos/demisto/content-private/dispatches'
GET_DISPATCH_WORKFLOWS_URL = 'https://api.github.com/repos/demisto/content-private/actions/runs'
WORKFLOW_HTML_URL = 'https://github.com/demisto/content-private/actions/runs'
GET_WORKFLOW_URL = 'https://api.github.com/repos/demisto/content-private/actions/runs/{:s}/jobs'

PRIVATE_REPO_WORKFLOW_ID_FILE = 'PRIVATE_REPO_WORKFLOW_ID.txt'

GET_WORKFLOWS_MAX_RETRIES = 3

GET_WORKFLOWS_TIMEOUT_THRESHOLD = 3600  # one hour


def get_modified_files(branch_name):
    files = []
    files_string = tools.run_command(f'git diff --name-only origin/master...{branch_name}')
    for line in files_string.split("\n"):
        if line:
            files.append(line)
    return files


def is_infrastructure_change(modified_files):
    for infra_file in modified_files:
        if infra_file in PRIVATE_BUILD_INFRA_SCRIPTS:
            return True

        path = os.path.dirname(infra_file)
        for infra_code_dir_path in PRIVATE_BUILD_INFRA_FOLDERS:
            if path.startswith(infra_code_dir_path):
                return True
    return False


def get_dispatch_workflows_ids(bearer_token, branch):
    res = requests.get(GET_DISPATCH_WORKFLOWS_URL,
                       headers={'Authorization': bearer_token},
                       params={'branch': branch, 'event': 'repository_dispatch'},
                       verify=False)
    if res.status_code != 200:
        logging.error(f'Failed to get private repo workflows, request to '
                      f'{GET_DISPATCH_WORKFLOWS_URL} failed with error: {str(res.content)}')
        sys.exit(1)

    try:
        workflows = json.loads(res.content)
    except ValueError:
        logging.error('Enable to parse private repo workflows response')
        sys.exit(1)

    workflows = workflows.get('workflow_runs', [])
    return [workflow.get('id') for workflow in workflows]


def main():
    install_logging("TriggerPrivateBuild.log")
    # get github_token parameter
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument('--github-token', help='Github token')
    arg_parser.add_argument('--commit-sha1', help='commit sha1 for creating the private repo')
    args = arg_parser.parse_args()
    bearer_token = f'Bearer {args.github_token}'

    # get branch name
    branches = tools.run_command("git branch")
    branch_name_regex = re.search(r"\* (.*)", branches)
    branch_name = branch_name_regex.group(1)

    file_validator = ValidateManager()
    file_validator.branch_name = 'trigger-private-build'
    modified_files, added_files, _, _ = file_validator.get_changed_files_from_git()
    print(modified_files)
    print(added_files)

    modified_files = get_modified_files(branch_name)

    if is_infrastructure_change(modified_files):
        # get the workflows ids before triggering the build
        pre_existing_workflow_ids = get_dispatch_workflows_ids(bearer_token, 'master')

        # trigger private build
        payload = {'event_type': f'Trigger private build from content/{branch_name}',
                   'client_payload': {'commit_sha1': args.commit_sha1, 'is_infra_build': 'True'}}

        res = requests.post(TRIGGER_BUILD_URL,
                            headers={'Accept': 'application/vnd.github.everest-preview+json',
                                    'Authorization': bearer_token},
                            data=json.dumps(payload),
                            verify=False)

        if res.status_code != 204:
            logging.error(f'Failed to trigger private repo build, request to '
                          f'{TRIGGER_BUILD_URL} failed with error: {str(res.content)}')
            sys.exit(1)

        workflow_ids_diff = []
        for i in range(GET_WORKFLOWS_MAX_RETRIES):
            # wait 5 seconds and get the workflow ids again
            time.sleep(5)
            workflow_ids_after_dispatch = get_dispatch_workflows_ids(bearer_token, 'master')

            # compare with the first workflows list to get the current id
            workflow_ids_diff = [x for x in workflow_ids_after_dispatch if x not in pre_existing_workflow_ids]
            if workflow_ids_diff:
                break

        if len(workflow_ids_diff) == 1:
            workflow_id = workflow_ids_diff[0]
            logging.info(f'Private repo build triggered successfully, workflow id: {workflow_id}\n URL:'
                         f' {WORKFLOW_HTML_URL}/{workflow_id}')

            # write the workflow id to text file to use it in get_private_build_status.py
            with open(PRIVATE_REPO_WORKFLOW_ID_FILE, "w") as f:
                f.write(str(workflow_id))
            sys.exit(0)

        else:
            logging.error('Could not found the private repo workflow')
            sys.exit(1)

    else:
        logging.info('Build private repo skipped')


if __name__ == "__main__":
    main()
