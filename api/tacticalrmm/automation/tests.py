from unittest.mock import patch
from tacticalrmm.test import TacticalTestCase
from model_bakery import baker, seq
from itertools import cycle
from agents.models import Agent
from winupdate.models import WinUpdatePolicy

from .serializers import (
    PolicyTableSerializer,
    PolicySerializer,
    PolicyTaskStatusSerializer,
    PolicyOverviewSerializer,
    PolicyCheckStatusSerializer,
    PolicyCheckSerializer,
    AutoTasksFieldSerializer,
)


class TestPolicyViews(TacticalTestCase):
    def setUp(self):
        self.authenticate()
        self.setup_coresettings()

    def test_get_all_policies(self):
        url = "/automation/policies/"

        policies = baker.make("automation.Policy", _quantity=3)
        resp = self.client.get(url, format="json")
        serializer = PolicyTableSerializer(policies, many=True)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, serializer.data)

        self.check_not_authenticated("get", url)

    def test_get_policy(self):
        # returns 404 for invalid policy pk
        resp = self.client.get("/automation/policies/500/", format="json")
        self.assertEqual(resp.status_code, 404)

        policy = baker.make("automation.Policy")
        url = f"/automation/policies/{policy.pk}/"

        resp = self.client.get(url, format="json")
        serializer = PolicySerializer(policy)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, serializer.data)

        self.check_not_authenticated("get", url)

    def test_add_policy(self):
        url = "/automation/policies/"

        data = {
            "name": "Test Policy",
            "desc": "policy desc",
            "active": True,
            "enforced": False,
        }

        resp = self.client.post(url, data, format="json")
        self.assertEqual(resp.status_code, 200)

        # running again should fail since names are unique
        resp = self.client.post(url, data, format="json")
        self.assertEqual(resp.status_code, 400)

        # create policy with tasks and checks
        policy = baker.make("automation.Policy")
        self.create_checks(policy=policy)
        baker.make("autotasks.AutomatedTask", policy=policy, _quantity=3)

        # test copy tasks and checks to another policy
        data = {
            "name": "Test Copy Policy",
            "desc": "policy desc",
            "active": True,
            "enforced": False,
            "copyId": policy.pk,
        }

        resp = self.client.post(f"/automation/policies/", data, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(policy.autotasks.count(), 3)
        self.assertEqual(policy.policychecks.count(), 7)

        self.check_not_authenticated("post", url)

    @patch("automation.tasks.generate_agent_checks_from_policies_task.delay")
    def test_update_policy(self, generate_agent_checks_from_policies_task):
        # returns 404 for invalid policy pk
        resp = self.client.put("/automation/policies/500/", format="json")
        self.assertEqual(resp.status_code, 404)

        policy = baker.make("automation.Policy", active=True, enforced=False)
        url = f"/automation/policies/{policy.pk}/"

        data = {
            "name": "Test Policy Update",
            "desc": "policy desc Update",
            "active": True,
            "enforced": False,
        }

        resp = self.client.put(url, data, format="json")
        self.assertEqual(resp.status_code, 200)

        # only called if active or enforced are updated
        generate_agent_checks_from_policies_task.assert_not_called()

        data = {
            "name": "Test Policy Update",
            "desc": "policy desc Update",
            "active": False,
            "enforced": False,
        }

        resp = self.client.put(url, data, format="json")
        self.assertEqual(resp.status_code, 200)
        generate_agent_checks_from_policies_task.assert_called_with(
            policypk=policy.pk, create_tasks=True
        )

        self.check_not_authenticated("put", url)

    @patch("automation.tasks.generate_agent_checks_task.delay")
    def test_delete_policy(self, generate_agent_checks_task):
        # returns 404 for invalid policy pk
        resp = self.client.delete("/automation/policies/500/", format="json")
        self.assertEqual(resp.status_code, 404)

        # setup data
        policy = baker.make("automation.Policy")
        site = baker.make("clients.Site")
        agents = baker.make_recipe(
            "agents.agent", site=site, policy=policy, _quantity=3
        )
        url = f"/automation/policies/{policy.pk}/"

        resp = self.client.delete(url, format="json")
        self.assertEqual(resp.status_code, 200)

        generate_agent_checks_task.assert_called_with(
            [agent.pk for agent in agents], create_tasks=True
        )

        self.check_not_authenticated("delete", url)

    def test_get_all_policy_tasks(self):
        # create policy with tasks
        policy = baker.make("automation.Policy")
        tasks = baker.make("autotasks.AutomatedTask", policy=policy, _quantity=3)
        url = f"/automation/{policy.pk}/policyautomatedtasks/"

        resp = self.client.get(url, format="json")
        serializer = AutoTasksFieldSerializer(tasks, many=True)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, serializer.data)
        self.assertEqual(len(resp.data), 3)

        self.check_not_authenticated("get", url)

    def test_get_all_policy_checks(self):

        # setup data
        policy = baker.make("automation.Policy")
        checks = self.create_checks(policy=policy)

        url = f"/automation/{policy.pk}/policychecks/"

        resp = self.client.get(url, format="json")
        serializer = PolicyCheckSerializer(checks, many=True)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, serializer.data)
        self.assertEqual(len(resp.data), 7)

        self.check_not_authenticated("get", url)

    def test_get_policy_check_status(self):
        # setup data
        site = baker.make("clients.Site")
        agent = baker.make_recipe("agents.agent", site=site)
        policy = baker.make("automation.Policy")
        policy_diskcheck = baker.make_recipe("checks.diskspace_check", policy=policy)
        managed_check = baker.make_recipe(
            "checks.diskspace_check",
            agent=agent,
            managed_by_policy=True,
            parent_check=policy_diskcheck.pk,
        )
        url = f"/automation/policycheckstatus/{policy_diskcheck.pk}/check/"

        resp = self.client.patch(url, format="json")
        serializer = PolicyCheckStatusSerializer([managed_check], many=True)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, serializer.data)
        self.check_not_authenticated("patch", url)

    def test_policy_overview(self):
        from clients.models import Client

        url = "/automation/policies/overview/"

        policies = baker.make(
            "automation.Policy", active=cycle([True, False]), _quantity=5
        )
        clients = baker.make(
            "clients.Client",
            server_policy=cycle(policies),
            workstation_policy=cycle(policies),
            _quantity=5,
        )
        baker.make(
            "clients.Site",
            client=cycle(clients),
            server_policy=cycle(policies),
            workstation_policy=cycle(policies),
            _quantity=4,
        )

        baker.make("clients.Site", client=cycle(clients), _quantity=3)
        resp = self.client.get(url, format="json")
        clients = Client.objects.all()
        serializer = PolicyOverviewSerializer(clients, many=True)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, serializer.data)

        self.check_not_authenticated("get", url)

    def test_get_related(self):
        policy = baker.make("automation.Policy")
        url = f"/automation/policies/{policy.pk}/related/"

        resp = self.client.get(url, format="json")

        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data["server_clients"], list)
        self.assertIsInstance(resp.data["workstation_clients"], list)
        self.assertIsInstance(resp.data["server_sites"], list)
        self.assertIsInstance(resp.data["workstation_sites"], list)
        self.assertIsInstance(resp.data["agents"], list)

        self.check_not_authenticated("get", url)

    def test_get_policy_task_status(self):

        # policy with a task
        policy = baker.make("automation.Policy")
        task = baker.make("autotasks.AutomatedTask", policy=policy)

        # create policy managed tasks
        policy_tasks = baker.make(
            "autotasks.AutomatedTask", parent_task=task.id, _quantity=5
        )

        url = f"/automation/policyautomatedtaskstatus/{task.id}/task/"

        serializer = PolicyTaskStatusSerializer(policy_tasks, many=True)
        resp = self.client.patch(url, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, serializer.data)
        self.assertEqual(len(resp.data), 5)

        self.check_not_authenticated("patch", url)

    @patch("automation.tasks.run_win_policy_autotask_task.delay")
    def test_run_win_task(self, mock_task):

        # create managed policy tasks
        tasks = baker.make(
            "autotasks.AutomatedTask",
            managed_by_policy=True,
            parent_task=1,
            _quantity=6,
        )
        url = "/automation/runwintask/1/"
        resp = self.client.put(url, format="json")
        self.assertEqual(resp.status_code, 200)

        mock_task.assert_called_once_with([task.pk for task in tasks])

        self.check_not_authenticated("put", url)

    def test_create_new_patch_policy(self):
        url = "/automation/winupdatepolicy/"

        # test policy doesn't exist
        data = {"policy": 500}
        resp = self.client.post(url, data, format="json")
        self.assertEqual(resp.status_code, 404)

        policy = baker.make("automation.Policy")

        data = {
            "policy": policy.pk,
            "critical": "approve",
            "important": "approve",
            "moderate": "ignore",
            "low": "ignore",
            "other": "approve",
            "run_time_hour": 3,
            "run_time_frequency": "daily",
            "run_time_days": [0, 3, 5],
            "run_time_day": "15",
            "reboot_after_install": "always",
        }

        resp = self.client.post(url, data, format="json")
        self.assertEqual(resp.status_code, 200)

        self.check_not_authenticated("post", url)

    def test_update_patch_policy(self):

        # test policy doesn't exist
        resp = self.client.put("/automation/winupdatepolicy/500/", format="json")
        self.assertEqual(resp.status_code, 404)

        policy = baker.make("automation.Policy")
        patch_policy = baker.make("winupdate.WinUpdatePolicy", policy=policy)
        url = f"/automation/winupdatepolicy/{patch_policy.pk}/"

        data = {
            "id": patch_policy.pk,
            "policy": policy.pk,
            "critical": "approve",
            "important": "approve",
            "moderate": "ignore",
            "low": "ignore",
            "other": "approve",
            "run_time_days": [4, 5, 6],
        }

        resp = self.client.put(url, data, format="json")
        self.assertEqual(resp.status_code, 200)

        self.check_not_authenticated("put", url)

    def test_reset_patch_policy(self):
        url = "/automation/winupdatepolicy/reset/"

        inherit_fields = {
            "critical": "inherit",
            "important": "inherit",
            "moderate": "inherit",
            "low": "inherit",
            "other": "inherit",
            "run_time_frequency": "inherit",
            "reboot_after_install": "inherit",
            "reprocess_failed_inherit": True,
        }

        clients = baker.make("clients.Client", _quantity=6)
        sites = baker.make("clients.Site", client=cycle(clients), _quantity=10)
        agents = baker.make_recipe(
            "agents.agent",
            site=cycle(sites),
            _quantity=6,
        )

        # create patch policies
        baker.make_recipe(
            "winupdate.winupdate_approve", agent=cycle(agents), _quantity=6
        )

        # test reset agents in site
        data = {"site": sites[0].id}

        resp = self.client.patch(url, data, format="json")
        self.assertEqual(resp.status_code, 200)

        agents = Agent.objects.filter(site=sites[0])

        for agent in agents:
            for k, v in inherit_fields.items():
                self.assertEqual(getattr(agent.winupdatepolicy.get(), k), v)

        # test reset agents in client
        data = {"client": clients[1].id}

        resp = self.client.patch(url, data, format="json")
        self.assertEqual(resp.status_code, 200)

        agents = Agent.objects.filter(site__client=clients[1])

        for agent in agents:
            for k, v in inherit_fields.items():
                self.assertEqual(getattr(agent.winupdatepolicy.get(), k), v)

        # test reset all agents
        data = {}

        resp = self.client.patch(url, data, format="json")
        self.assertEqual(resp.status_code, 200)

        agents = Agent.objects.all()
        for agent in agents:
            for k, v in inherit_fields.items():
                self.assertEqual(getattr(agent.winupdatepolicy.get(), k), v)

        self.check_not_authenticated("patch", url)

    def test_delete_patch_policy(self):
        # test patch policy doesn't exist
        resp = self.client.delete("/automation/winupdatepolicy/500/", format="json")
        self.assertEqual(resp.status_code, 404)

        winupdate_policy = baker.make_recipe(
            "winupdate.winupdate_policy", policy__name="Test Policy"
        )
        url = f"/automation/winupdatepolicy/{winupdate_policy.pk}/"

        resp = self.client.delete(url, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            WinUpdatePolicy.objects.filter(pk=winupdate_policy.pk).exists()
        )

        self.check_not_authenticated("delete", url)


class TestPolicyTasks(TacticalTestCase):
    def setUp(self):
        self.authenticate()
        self.setup_coresettings()

    def test_policy_related(self):

        # Get Site and Client from an agent in list
        clients = baker.make("clients.Client", _quantity=5)
        sites = baker.make("clients.Site", client=cycle(clients), _quantity=25)
        server_agents = baker.make_recipe(
            "agents.server_agent",
            site=cycle(sites),
            _quantity=25,
        )
        workstation_agents = baker.make_recipe(
            "agents.workstation_agent",
            site=cycle(sites),
            _quantity=25,
        )

        policy = baker.make("automation.Policy", active=True)

        # Add Client to Policy
        policy.server_clients.add(server_agents[13].client)
        policy.workstation_clients.add(workstation_agents[15].client)

        resp = self.client.get(
            f"/automation/policies/{policy.pk}/related/", format="json"
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEquals(len(resp.data["server_clients"]), 1)
        self.assertEquals(len(resp.data["server_sites"]), 5)
        self.assertEquals(len(resp.data["workstation_clients"]), 1)
        self.assertEquals(len(resp.data["workstation_sites"]), 5)
        self.assertEquals(len(resp.data["agents"]), 10)

        # Add Site to Policy and the agents and sites length shouldn't change
        policy.server_sites.add(server_agents[13].site)
        policy.workstation_sites.add(workstation_agents[15].site)
        self.assertEquals(len(resp.data["server_sites"]), 5)
        self.assertEquals(len(resp.data["workstation_sites"]), 5)
        self.assertEquals(len(resp.data["agents"]), 10)

        # Add Agent to Policy and the agents length shouldn't change
        policy.agents.add(server_agents[13])
        policy.agents.add(workstation_agents[15])
        self.assertEquals(len(resp.data["agents"]), 10)

    def test_generating_agent_policy_checks(self):
        from .tasks import generate_agent_checks_from_policies_task

        # setup data
        policy = baker.make("automation.Policy", active=True)
        checks = self.create_checks(policy=policy)
        agent = baker.make_recipe("agents.agent", policy=policy)

        # test policy assigned to agent
        generate_agent_checks_from_policies_task(policy.id)

        # make sure all checks were created. should be 7
        agent_checks = Agent.objects.get(pk=agent.id).agentchecks.all()
        self.assertEquals(len(agent_checks), 7)

        # make sure checks were copied correctly
        for check in agent_checks:

            self.assertTrue(check.managed_by_policy)
            if check.check_type == "diskspace":
                self.assertEqual(check.parent_check, checks[0].id)
                self.assertEqual(check.disk, checks[0].disk)
                self.assertEqual(check.error_threshold, checks[0].error_threshold)
                self.assertEqual(check.warning_threshold, checks[0].warning_threshold)
            elif check.check_type == "ping":
                self.assertEqual(check.parent_check, checks[1].id)
                self.assertEqual(check.ip, checks[1].ip)
            elif check.check_type == "cpuload":
                self.assertEqual(check.parent_check, checks[2].id)
                self.assertEqual(check.error_threshold, checks[0].error_threshold)
                self.assertEqual(check.warning_threshold, checks[0].warning_threshold)
            elif check.check_type == "memory":
                self.assertEqual(check.parent_check, checks[3].id)
                self.assertEqual(check.error_threshold, checks[0].error_threshold)
                self.assertEqual(check.warning_threshold, checks[0].warning_threshold)
            elif check.check_type == "winsvc":
                self.assertEqual(check.parent_check, checks[4].id)
                self.assertEqual(check.svc_name, checks[4].svc_name)
                self.assertEqual(check.svc_display_name, checks[4].svc_display_name)
                self.assertEqual(check.svc_policy_mode, checks[4].svc_policy_mode)
            elif check.check_type == "script":
                self.assertEqual(check.parent_check, checks[5].id)
                self.assertEqual(check.script, checks[5].script)
            elif check.check_type == "eventlog":
                self.assertEqual(check.parent_check, checks[6].id)
                self.assertEqual(check.event_id, checks[6].event_id)
                self.assertEqual(check.event_type, checks[6].event_type)

    def test_generating_agent_policy_checks_with_enforced(self):
        from .tasks import generate_agent_checks_from_policies_task

        # setup data
        policy = baker.make("automation.Policy", active=True, enforced=True)
        script = baker.make_recipe("scripts.script")
        self.create_checks(policy=policy, script=script)
        site = baker.make("clients.Site")
        agent = baker.make_recipe("agents.agent", site=site, policy=policy)
        self.create_checks(agent=agent, script=script)

        generate_agent_checks_from_policies_task(policy.id, create_tasks=True)

        # make sure each agent check says overriden_by_policy
        self.assertEqual(Agent.objects.get(pk=agent.id).agentchecks.count(), 14)
        self.assertEqual(
            Agent.objects.get(pk=agent.id)
            .agentchecks.filter(overriden_by_policy=True)
            .count(),
            7,
        )

    @patch("automation.tasks.generate_agent_checks_by_location_task.delay")
    def test_generating_agent_policy_checks_by_location(
        self, generate_agent_checks_by_location_task
    ):
        from automation.tasks import (
            generate_agent_checks_by_location_task as generate_agent_checks,
        )

        # setup data
        policy = baker.make("automation.Policy", active=True)
        self.create_checks(policy=policy)

        baker.make(
            "autotasks.AutomatedTask", policy=policy, name=seq("Task"), _quantity=3
        )

        server_agent = baker.make_recipe("agents.server_agent")
        workstation_agent = baker.make_recipe("agents.workstation_agent")

        # no checks should be preset on agents
        self.assertEqual(Agent.objects.get(pk=server_agent.id).agentchecks.count(), 0)
        self.assertEqual(
            Agent.objects.get(pk=workstation_agent.id).agentchecks.count(), 0
        )

        # set workstation policy on client and policy checks should be there
        workstation_agent.client.workstation_policy = policy
        workstation_agent.client.save()

        # should trigger task in save method on core
        generate_agent_checks_by_location_task.assert_called_with(
            location={"site__client_id": workstation_agent.client.pk},
            mon_type="workstation",
            create_tasks=True,
        )
        generate_agent_checks_by_location_task.reset_mock()

        generate_agent_checks(
            location={"site__client_id": workstation_agent.client.pk},
            mon_type="workstation",
            create_tasks=True,
        )

        # make sure the checks were added
        self.assertEqual(
            Agent.objects.get(pk=workstation_agent.id).agentchecks.count(), 7
        )
        self.assertEqual(Agent.objects.get(pk=server_agent.id).agentchecks.count(), 0)

        # remove workstation policy from client
        workstation_agent.client.workstation_policy = None
        workstation_agent.client.save()

        # should trigger task in save method on core
        generate_agent_checks_by_location_task.assert_called_with(
            location={"site__client_id": workstation_agent.client.pk},
            mon_type="workstation",
            create_tasks=True,
        )
        generate_agent_checks_by_location_task.reset_mock()

        generate_agent_checks(
            location={"site__client_id": workstation_agent.client.pk},
            mon_type="workstation",
            create_tasks=True,
        )

        # make sure the checks were removed
        self.assertEqual(
            Agent.objects.get(pk=workstation_agent.id).agentchecks.count(), 0
        )
        self.assertEqual(Agent.objects.get(pk=server_agent.id).agentchecks.count(), 0)

        # set server policy on client and policy checks should be there
        server_agent.client.server_policy = policy
        server_agent.client.save()

        # should trigger task in save method on core
        generate_agent_checks_by_location_task.assert_called_with(
            location={"site__client_id": server_agent.client.pk},
            mon_type="server",
            create_tasks=True,
        )
        generate_agent_checks_by_location_task.reset_mock()

        generate_agent_checks(
            location={"site__client_id": server_agent.client.pk},
            mon_type="server",
            create_tasks=True,
        )

        # make sure checks were added
        self.assertEqual(Agent.objects.get(pk=server_agent.id).agentchecks.count(), 7)
        self.assertEqual(
            Agent.objects.get(pk=workstation_agent.id).agentchecks.count(), 0
        )

        # remove server policy from client
        server_agent.client.server_policy = None
        server_agent.client.save()

        # should trigger task in save method on core
        generate_agent_checks_by_location_task.assert_called_with(
            location={"site__client_id": server_agent.client.pk},
            mon_type="server",
            create_tasks=True,
        )
        generate_agent_checks_by_location_task.reset_mock()

        generate_agent_checks(
            location={"site__client_id": server_agent.client.pk},
            mon_type="server",
            create_tasks=True,
        )

        # make sure checks were removed
        self.assertEqual(Agent.objects.get(pk=server_agent.id).agentchecks.count(), 0)
        self.assertEqual(
            Agent.objects.get(pk=workstation_agent.id).agentchecks.count(), 0
        )

        # set workstation policy on site and policy checks should be there
        workstation_agent.site.workstation_policy = policy
        workstation_agent.site.save()

        # should trigger task in save method on core
        generate_agent_checks_by_location_task.assert_called_with(
            location={"site_id": workstation_agent.site.pk},
            mon_type="workstation",
            create_tasks=True,
        )
        generate_agent_checks_by_location_task.reset_mock()

        generate_agent_checks(
            location={"site_id": workstation_agent.site.pk},
            mon_type="workstation",
            create_tasks=True,
        )

        # make sure checks were added on workstation
        self.assertEqual(
            Agent.objects.get(pk=workstation_agent.id).agentchecks.count(), 7
        )
        self.assertEqual(Agent.objects.get(pk=server_agent.id).agentchecks.count(), 0)

        # remove workstation policy from site
        workstation_agent.site.workstation_policy = None
        workstation_agent.site.save()

        # should trigger task in save method on core
        generate_agent_checks_by_location_task.assert_called_with(
            location={"site_id": workstation_agent.site.pk},
            mon_type="workstation",
            create_tasks=True,
        )
        generate_agent_checks_by_location_task.reset_mock()

        generate_agent_checks(
            location={"site_id": workstation_agent.site.pk},
            mon_type="workstation",
            create_tasks=True,
        )

        # make sure checks were removed
        self.assertEqual(
            Agent.objects.get(pk=workstation_agent.id).agentchecks.count(), 0
        )
        self.assertEqual(Agent.objects.get(pk=server_agent.id).agentchecks.count(), 0)

        # set server policy on site and policy checks should be there
        server_agent.site.server_policy = policy
        server_agent.site.save()

        # should trigger task in save method on core
        generate_agent_checks_by_location_task.assert_called_with(
            location={"site_id": server_agent.site.pk},
            mon_type="server",
            create_tasks=True,
        )
        generate_agent_checks_by_location_task.reset_mock()

        generate_agent_checks(
            location={"site_id": server_agent.site.pk},
            mon_type="server",
            create_tasks=True,
        )

        # make sure checks were added
        self.assertEqual(Agent.objects.get(pk=server_agent.id).agentchecks.count(), 7)
        self.assertEqual(
            Agent.objects.get(pk=workstation_agent.id).agentchecks.count(), 0
        )

        # remove server policy from site
        server_agent.site.server_policy = None
        server_agent.site.save()

        # should trigger task in save method on core
        generate_agent_checks_by_location_task.assert_called_with(
            location={"site_id": server_agent.site.pk},
            mon_type="server",
            create_tasks=True,
        )
        generate_agent_checks_by_location_task.reset_mock()

        generate_agent_checks(
            location={"site_id": server_agent.site.pk},
            mon_type="server",
            create_tasks=True,
        )

        # make sure checks were removed
        self.assertEqual(Agent.objects.get(pk=server_agent.id).agentchecks.count(), 0)
        self.assertEqual(
            Agent.objects.get(pk=workstation_agent.id).agentchecks.count(), 0
        )

    @patch("automation.tasks.generate_all_agent_checks_task.delay")
    def test_generating_policy_checks_for_all_agents(
        self, generate_all_agent_checks_task
    ):
        from .tasks import generate_all_agent_checks_task as generate_all_checks
        from core.models import CoreSettings

        # setup data
        policy = baker.make("automation.Policy", active=True)
        self.create_checks(policy=policy)

        server_agents = baker.make_recipe("agents.server_agent", _quantity=3)
        workstation_agents = baker.make_recipe("agents.workstation_agent", _quantity=4)
        core = CoreSettings.objects.first()
        core.server_policy = policy
        core.save()

        generate_all_agent_checks_task.assert_called_with(
            mon_type="server", create_tasks=True
        )
        generate_all_agent_checks_task.reset_mock()
        generate_all_checks(mon_type="server", create_tasks=True)

        # all servers should have 7 checks
        for agent in server_agents:
            self.assertEqual(Agent.objects.get(pk=agent.id).agentchecks.count(), 7)

        for agent in workstation_agents:
            self.assertEqual(Agent.objects.get(pk=agent.id).agentchecks.count(), 0)

        core.server_policy = None
        core.workstation_policy = policy
        core.save()

        generate_all_agent_checks_task.assert_any_call(
            mon_type="workstation", create_tasks=True
        )
        generate_all_agent_checks_task.assert_any_call(
            mon_type="server", create_tasks=True
        )
        generate_all_agent_checks_task.reset_mock()
        generate_all_checks(mon_type="server", create_tasks=True)
        generate_all_checks(mon_type="workstation", create_tasks=True)

        # all workstations should have 7 checks
        for agent in server_agents:
            self.assertEqual(Agent.objects.get(pk=agent.id).agentchecks.count(), 0)

        for agent in workstation_agents:
            self.assertEqual(Agent.objects.get(pk=agent.id).agentchecks.count(), 7)

        core.workstation_policy = None
        core.save()

        generate_all_agent_checks_task.assert_called_with(
            mon_type="workstation", create_tasks=True
        )
        generate_all_agent_checks_task.reset_mock()
        generate_all_checks(mon_type="workstation", create_tasks=True)

        # nothing should have the checks
        for agent in server_agents:
            self.assertEqual(Agent.objects.get(pk=agent.id).agentchecks.count(), 0)

        for agent in workstation_agents:
            self.assertEqual(Agent.objects.get(pk=agent.id).agentchecks.count(), 0)

    def test_delete_policy_check(self):
        from .tasks import delete_policy_check_task
        from .models import Policy

        policy = baker.make("automation.Policy", active=True)
        self.create_checks(policy=policy)
        agent = baker.make_recipe("agents.server_agent", policy=policy)

        # make sure agent has 7 checks
        self.assertEqual(Agent.objects.get(pk=agent.id).agentchecks.count(), 7)

        # pick a policy check and delete it from the agent
        policy_check_id = Policy.objects.get(pk=policy.id).policychecks.first().id

        delete_policy_check_task(policy_check_id)

        # make sure policy check doesn't exist on agent
        self.assertEqual(Agent.objects.get(pk=agent.id).agentchecks.count(), 6)
        self.assertFalse(
            Agent.objects.get(pk=agent.id)
            .agentchecks.filter(parent_check=policy_check_id)
            .exists()
        )

    def update_policy_check_fields(self):
        from .tasks import update_policy_check_fields_task
        from .models import Policy

        policy = baker.make("automation.Policy", active=True)
        self.create_checks(policy=policy)
        agent = baker.make_recipe("agents.server_agent", policy=policy)

        # make sure agent has 7 checks
        self.assertEqual(Agent.objects.get(pk=agent.id).agentchecks.count(), 7)

        # pick a policy check and update it with new values
        ping_check = (
            Policy.objects.get(pk=policy.id)
            .policychecks.filter(check_type="ping")
            .first()
        )
        ping_check.ip = "12.12.12.12"
        ping_check.save()

        update_policy_check_fields_task(ping_check.id)

        # make sure policy check was updated on the agent
        self.assertEquals(
            Agent.objects.get(pk=agent.id)
            .agentchecks.filter(parent_check=ping_check.id)
            .ip,
            "12.12.12.12",
        )

    def test_generate_agent_tasks(self):
        from .tasks import generate_agent_tasks_from_policies_task

        # create test data
        policy = baker.make("automation.Policy", active=True)
        tasks = baker.make(
            "autotasks.AutomatedTask", policy=policy, name=seq("Task"), _quantity=3
        )
        agent = baker.make_recipe("agents.server_agent", policy=policy)

        generate_agent_tasks_from_policies_task(policy.id)

        agent_tasks = Agent.objects.get(pk=agent.id).autotasks.all()

        # make sure there are 3 agent tasks
        self.assertEqual(len(agent_tasks), 3)

        for task in agent_tasks:
            self.assertTrue(task.managed_by_policy)
            if task.name == "Task1":
                self.assertEqual(task.parent_task, tasks[0].id)
                self.assertEqual(task.name, tasks[0].name)
            if task.name == "Task2":
                self.assertEqual(task.parent_task, tasks[1].id)
                self.assertEqual(task.name, tasks[1].name)
            if task.name == "Task3":
                self.assertEqual(task.parent_task, tasks[2].id)
                self.assertEqual(task.name, tasks[2].name)

    @patch("autotasks.tasks.delete_win_task_schedule.delay")
    def test_delete_policy_tasks(self, delete_win_task_schedule):
        from .tasks import delete_policy_autotask_task

        policy = baker.make("automation.Policy", active=True)
        tasks = baker.make("autotasks.AutomatedTask", policy=policy, _quantity=3)
        agent = baker.make_recipe("agents.server_agent", policy=policy)

        delete_policy_autotask_task(tasks[0].id)

        delete_win_task_schedule.assert_called_with(
            agent.autotasks.get(parent_task=tasks[0].id).id
        )

    @patch("autotasks.tasks.run_win_task.delay")
    def test_run_policy_task(self, run_win_task):
        from .tasks import run_win_policy_autotask_task

        tasks = baker.make("autotasks.AutomatedTask", _quantity=3)

        run_win_policy_autotask_task([task.id for task in tasks])

        run_win_task.side_effect = [task.id for task in tasks]
        self.assertEqual(run_win_task.call_count, 3)
        for task in tasks:
            run_win_task.assert_any_call(task.id)

    @patch("autotasks.tasks.enable_or_disable_win_task.delay")
    def test_update_policy_tasks(self, enable_or_disable_win_task):
        from .tasks import update_policy_task_fields_task

        # setup data
        policy = baker.make("automation.Policy", active=True)
        tasks = baker.make(
            "autotasks.AutomatedTask", enabled=True, policy=policy, _quantity=3
        )
        agent = baker.make_recipe("agents.server_agent", policy=policy)

        tasks[0].enabled = False
        tasks[0].save()

        update_policy_task_fields_task(tasks[0].id)
        enable_or_disable_win_task.assert_not_called()

        self.assertFalse(agent.autotasks.get(parent_task=tasks[0].id).enabled)

        update_policy_task_fields_task(tasks[0].id, update_agent=True)
        enable_or_disable_win_task.assert_called_with(
            agent.autotasks.get(parent_task=tasks[0].id).id, False
        )

    @patch("agents.models.Agent.generate_tasks_from_policies")
    @patch("agents.models.Agent.generate_checks_from_policies")
    def test_generate_agent_checks_with_agentpks(self, generate_checks, generate_tasks):
        from automation.tasks import generate_agent_checks_task

        agents = baker.make_recipe("agents.agent", _quantity=5)

        # reset because creating agents triggers it
        generate_checks.reset_mock()
        generate_tasks.reset_mock()

        generate_agent_checks_task([agent.pk for agent in agents])
        self.assertEquals(generate_checks.call_count, 5)
        generate_tasks.assert_not_called()
        generate_checks.reset_mock()

        generate_agent_checks_task([agent.pk for agent in agents], create_tasks=True)
        self.assertEquals(generate_checks.call_count, 5)
        self.assertEquals(generate_checks.call_count, 5)
