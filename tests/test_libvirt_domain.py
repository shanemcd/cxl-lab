"""Structural regression tests for native libvirt and CXL passthrough XML."""
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from ansible.plugins.filter import core, mathstuff
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "ansible_collections/shanemcd/cxl_lab/roles/vms/templates"
QEMU = {"q": "http://libvirt.org/schemas/domain/qemu/1.0"}


class DomainTests(unittest.TestCase):
    def render(self, kind):
        env = Environment(undefined=StrictUndefined)
        env.filters.update(to_uuid=core.to_uuid, bool=core.to_bool,
                           human_to_bytes=mathstuff.human_to_bytes)
        node = dict(name="test-" + kind, kind=kind, ram="16G", vcpu=4,
                    mac_suffix="14", vmem_size="4G", cxl_bus_nr=12)
        return ET.fromstring(env.from_string((TEMPLATES / "domain.xml.j2").read_text()).render(
            node=node, libvirt_runtime_dir="/var/lib/libvirt/images/test-lab",
            qemu_binary_path="/opt/qemu-test/bin/qemu-system-x86_64",
            vms_master_iso_name="master.iso", mac_base="52:54:00:0c:0c",
            libvirt_network_name="test-lab", vms_cxl_pxb_id="pxb0",
            vms_cxl_root_port_id="rp0", vms_cxl_type3_id="cxl-mem0",
            vmem_interleave_granularity=256))

    def test_cxl_is_memoryless_node_one_with_noncolliding_pci(self):
        xml = self.render("worker-cxl")
        self.assertEqual(xml.find("cpu/numa/cell").get("cpus"), "0-3")
        self.assertEqual(xml.find("cpu/numa/cell").get("id"), "0")
        args = [a.get("value") for a in xml.findall("q:commandline/q:arg", QEMU)]
        self.assertIn("node,nodeid=1", args)
        self.assertIn("memory-backend-ram,id=vmem0,share=on,size=4G", args)
        self.assertIn("pxb-cxl,bus=pcie.0,addr=0x08,id=pxb0,bus_nr=12,numa_node=1", args)
        self.assertTrue(any("cxl-fmw.0.targets.0=pxb0" in a for a in args))
        self.assertTrue(any(a.startswith("cxl-type3,") for a in args))
        self.assertFalse(any("host-nodes" in a for a in args))

    def test_dram_worker_has_no_cxl_arguments(self):
        xml = self.render("worker-dram")
        self.assertEqual(xml.findall("q:commandline/q:arg", QEMU), [])
        self.assertIsNone(xml.find("cpu/numa"))

    def test_master_boots_disk_before_iso(self):
        xml = self.render("master")
        disks = xml.findall("devices/disk")
        self.assertEqual([d.find("boot").get("order") for d in disks], ["1", "2"])
        self.assertEqual(disks[0].find("target").get("dev"), "vda")
        self.assertEqual(disks[0].find("address").get("slot"), "0x02")
        self.assertEqual(xml.find("devices/interface/address").get("slot"), "0x03")
        self.assertEqual(xml.find("sysinfo/entry").get("name"), "opt/com.coreos/config")
        self.assertTrue(xml.find("sysinfo/entry").get("file").startswith("/var/lib/libvirt/images/"))
        self.assertEqual(xml.find("devices/serial").get("type"), "pty")
        self.assertTrue(xml.find("devices/serial/log").get("file").startswith("/var/log/libvirt/qemu/"))
        self.assertEqual(xml.findtext("on_reboot"), "restart")


if __name__ == "__main__":
    unittest.main()
