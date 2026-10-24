"""Evidence required before completing an explicitly verified repair task."""

class CompletionGate:
    def __init__(self, agent):
        self.agent = agent
        self.command = getattr(agent, 'verification_command', None)
        self.enabled = bool(self.command and not agent.read_only)
        self.snapshot = None
        self.state = {'command':self.command, 'required':self.enabled,
                      'status':'not_run' if self.enabled else 'not_required', 'exit_code':None}
        agent.last_verification = self.state

    def observe(self, name, args, metadata):
        if not self.enabled:
            return
        if name=='run_shell' and str(args.get('command','')).strip()==self.command:
            code = metadata.get('exit_code')
            self.state['exit_code'] = code
            if metadata.get('tool_error_code')=='approval_denied':
                self.state['status']='blocked'
            elif code==0 and metadata.get('tool_status')=='ok':
                self.state['status']='passed'
                self.snapshot=self.agent.capture_workspace_snapshot()
            else:
                self.state['status']='failed'
        elif metadata.get('workspace_changed') and self.state['status']=='passed':
            self.state['status']='stale'

    def problem(self):
        if not self.enabled:
            return None
        if self.state['status']=='passed' and self.snapshot!=self.agent.capture_workspace_snapshot():
            self.state['status']='stale'
        if self.state['status']=='passed':
            return None
        return (f"Completion rejected: verification is {self.state['status']}. "
                f"Run the exact verification command {self.command!r} through run_shell after the latest edit. "
                "Read the actual failure output and repair implementation contracts, including return types; do not change tests. "
                "A statement that you will fix or test something is not execution evidence. "
                "Use a tool call now; return <final> only after verification passes. Approval is still required.")
