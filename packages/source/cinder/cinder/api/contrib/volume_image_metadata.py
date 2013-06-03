#   Copyright 2012 OpenStack, LLC.
#
#   Licensed under the Apache License, Version 2.0 (the "License"); you may
#   not use this file except in compliance with the License. You may obtain
#   a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#   License for the specific language governing permissions and limitations
#   under the License.

"""The Volume Image Metadata API extension."""

from cinder.api import extensions
from cinder.api.openstack import wsgi
from cinder.api import xmlutil
from cinder import volume


authorize = extensions.soft_extension_authorizer('volume',
                                                 'volume_image_metadata')


class VolumeImageMetadataController(wsgi.Controller):
    def __init__(self, *args, **kwargs):
        super(VolumeImageMetadataController, self).__init__(*args, **kwargs)
        self.volume_api = volume.API()

    def _add_image_metadata(self, context, resp_volume):
        try:
            image_meta = self.volume_api.get_volume_image_metadata(
                context, resp_volume)
        except Exception:
            return
        else:
            if image_meta:
                resp_volume['volume_image_metadata'] = dict(
                    image_meta.iteritems())

    @wsgi.extends
    def show(self, req, resp_obj, id):
        context = req.environ['cinder.context']
        if authorize(context):
            resp_obj.attach(xml=VolumeImageMetadataTemplate())
            self._add_image_metadata(context, resp_obj.obj['volume'])

    @wsgi.extends
    def detail(self, req, resp_obj):
        context = req.environ['cinder.context']
        if authorize(context):
            resp_obj.attach(xml=VolumesImageMetadataTemplate())
            for volume in list(resp_obj.obj.get('volumes', [])):
                self._add_image_metadata(context, volume)


class Volume_image_metadata(extensions.ExtensionDescriptor):
    """Show image metadata associated with the volume"""

    name = "VolumeImageMetadata"
    alias = "os-vol-image-meta"
    namespace = ("http://docs.openstack.org/volume/ext/"
                 "volume_image_metadata/api/v1")
    updated = "2012-12-07T00:00:00+00:00"

    def get_controller_extensions(self):
        controller = VolumeImageMetadataController()
        extension = extensions.ControllerExtension(self, 'volumes', controller)
        return [extension]


class VolumeImageMetadataMetadataTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement('volume_image_metadata',
                                       selector='volume_image_metadata')
        elem = xmlutil.SubTemplateElement(root, 'meta',
                                          selector=xmlutil.get_items)
        elem.set('key', 0)
        elem.text = 1

        return xmlutil.MasterTemplate(root, 1)


class VolumeImageMetadataTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement('volume', selector='volume')
        root.append(VolumeImageMetadataMetadataTemplate())

        alias = Volume_image_metadata.alias
        namespace = Volume_image_metadata.namespace

        return xmlutil.SlaveTemplate(root, 1, nsmap={alias: namespace})


class VolumesImageMetadataTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement('volumes')
        elem = xmlutil.SubTemplateElement(root, 'volume', selector='volume')
        elem.append(VolumeImageMetadataMetadataTemplate())

        alias = Volume_image_metadata.alias
        namespace = Volume_image_metadata.namespace

        return xmlutil.SlaveTemplate(root, 1, nsmap={alias: namespace})
