"""Tool for ingesting battlemaps into Fantasy Grounds campaigns"""


# Imports

from argparse import ArgumentParser
import json
from json.decoder import JSONDecodeError
import logging
import os
import re
import shutil
from xml.dom import minidom
import xml.etree.ElementTree as ET


# Globals

BOOLEAN_MAP = {
    'on': True,
    'off': False
}
BOOLEAN_MAP_INVERSE = {
    True: 'on',
    False: 'off'
}
CAMPAIGN_DIRS = (
    os.path.join(
        '{APPDATA}', 'Roaming', 'SmiteWorks', 'Fantasy Grounds',
        'campaigns', '{campaign}'
    ),
    os.path.join(
        '{HOME}', 'SmiteWorks', 'Fantasy Grounds', 'campaigns', '{campaign}'
    )
)
NEWLINE_FIX = re.compile(r'\n[\t]*\n[\t]*')
TEST_DIR = '/Volumes/backup/Project Backup/D&D Games/maps/neutral party'

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# Classes

class CampaignDB(object):
    """Object representing the campaign database"""

    def __init__(self, campaign_dir):

        logger.info("Initializing CampaignDB object for %s", campaign_dir)

        self.campaign_dir = campaign_dir
        self._filepath = os.path.join(self.campaign_dir, 'db.xml')
        try:
            self._xml = ET.parse(self.filepath)
        except FileNotFoundError:
            raise ValueError("No db.xml file found.")
        else:
            self._root = self._xml.getroot()
        self._maps = {}

    # Properties

    @property
    def filepath(self):
        return self._filepath

    @property
    def image(self):
        return self.root.find('image')

    @property
    def maps(self):
        if not self._maps:
            logger.info("Retrieving map data from DB")
            self._maps = self._get_maps()
        return self._maps

    @property
    def pretty_xml(self):
        return NEWLINE_FIX.sub(
            '',
            minidom.parseString(
                ET.tostring(self.root)
            ).toprettyxml(indent="\t")
        )

    @property
    def root(self):
        return self._root

    # Private Methods

    def _add_maps(self, maps):
        """Add given Map objects to the database.

        Args:
            maps (list[Map]): List of map objects.

        """
        maps = sorted(maps, key=lambda x: x.id)
        for map in maps:
            self.image.append(map.xml)

    def _get_maps(self):
        image_dict = {}
        for image_id in self.image:
            image_map = {}
            image = image_id.find('image')
            image_map['map_id'] = int(image_id.tag.split('-')[-1])
            image_map['name'] = image_id.find('name').text
            image_map['player_drawing'] = (
                BOOLEAN_MAP[image.find('allowplayerdrawing').text]
            )
            image_map['grid'] = (
                BOOLEAN_MAP[image.find('grid').text]
            )
            image_map['grid_size'] = self._translate_xml_xy(
                image.find('gridsize').text
            )
            try:
                image_map['grid_offset'] = self._translate_xml_xy(
                    image.find('gridoffset').text
                )
            except AttributeError:
                image_map['grid_offset'] = (0, 0)
            image_map['grid_snap'] = (
                BOOLEAN_MAP[image.find('gridsnap').text]
            )
            image_map['brush_size'] = self._translate_xml_xy(
                image.find('brushsize').text
            )
            image_map['occluders'] = []
            for layer in image.find('layers'):
                occluder = layer.find('occluders')
                if occluder:
                    image_map['occluders'].append(occluder)
            image_dict[image_map['name']] = image_map

            logger.debug(
                "Registered campaign information for %s", image_map['name']
            )

        return image_dict

    def _remove_existing_images(self):
        """Removes all images under the image element"""
        elems = [child for child in self.image]
        for child in elems:
            self.image.remove(child)

    @classmethod
    def _translate_xml_xy(cls, value):
        """Takes text in the form of 'x,y' and turns it into tuple w/ float"""
        x, y = value.split(',')
        try:
            return int(x), int(y)
        except ValueError:
            return float(x), float(y)

    # Public Methods

    def save_db(self):
        """Updates the database on disk"""
        with open(self.filepath, 'w') as f:
            f.write(self.pretty_xml)

    def update_db(self, maps):
        """Clears DB of images and repopulates.

        Args:
            maps (list[Map]): List of map objects.

        """
        self._remove_existing_images()
        self._add_maps(maps)


class Map(object):
    """Representing one map."""

    _campaign_defaults = {}
    _runtime_defaults = {}

    maps = {}

    def __init__(
            self, name, directory, campaign_db, map_id=None, parent_map=False,
            player_drawing=None, grid=None, grid_size=None, grid_offset=None,
            grid_snap=None, brush_size=None, occluders=None
    ):

        logger.info("Initializing a Map object for %s", name)

        self.name = name
        self.source_directory = directory
        self._xml = None

        self._id = map_id
        self._parent_map = False
        self._player_drawing = player_drawing
        self._grid = grid
        self._grid_size = grid_size
        self._grid_offset = grid_offset
        self._grid_snap = grid_snap
        self._brush_size = brush_size

        if occluders:
            self._occluders = occluders
        else:
            try:
                self._occluders = self._read_occluder_xml()
            except FileNotFoundError:  # TODO: Add malformed XML exception
                logger.debug("No XML Occluder file found for %s", self.name)
                self._occluders = []

        try:
            self._json_sidecar_settings = self._read_json_sidecar()
        except (FileNotFoundError, JSONDecodeError):
            logger.debug(
                "None or malformed JSON sidecar file found for %s", self.name
            )
            self._json_sidecar_settings = {}

        self.layers = self._generate_layers()
        self.campaign_db = campaign_db

        self.maps[self.name] = self

    # Properties

    @property
    def brush_size(self):
        if self._brush_size is None:
            self._brush_size = self._find_best_default('brush_size')
        return self._brush_size

    @property
    def destination_dir(self):
        return os.path.join(
            self.campaign_db.campaign_dir,
            'images',
            os.path.basename(self.source_directory)
        )

    @property
    def grid(self):
        if self._grid is None:
            self._grid = self._find_best_default('grid')
        return self._grid

    @property
    def grid_offset(self):
        if self._grid_offset is None:
            self._grid_offset = self._find_best_default('grid_offset')
        return self._grid_offset

    @property
    def grid_size(self):
        if self._grid_size is None:
            self._grid_size = self._find_best_default('grid_size')
        return self._grid_size

    @property
    def grid_snap(self):
        if self._grid_snap is None:
            self._grid_snap = self._find_best_default('grid_snap')
        return self._grid_snap

    @property
    def id(self):
        if self._id is None:
            # Existing map ID for map does not exist in DB
            if self.json_sidecar_settings.get('map_id') is None:
                # Map ID also not set in JSON
                return None
            else:
                self._id = self.json_sidecar_settings['map_id']
        return self._id

    @id.setter
    def id(self, value):
        if self._id is not None:
            raise RuntimeError("Cannot set id on a Map which already has an id")
        self._id = value

    @property
    def json_sidecar_settings(self):
        """Json overrides from JSON sidecar file"""
        return self._json_sidecar_settings

    @property
    def json_filepath(self):
        return os.path.join(self.source_directory, 'settings.json')

    @property
    def occluders(self):
        if self.parent_map:
            # Redirect occluders to parent map
            return self.maps[self.parent_map].occluders
        else:
            return self._occluders

    @property
    def occluder_xml_filepath(self):
        return os.path.join(self.source_directory, 'occluders.xml')

    @property
    def parent_map(self):
        if self._parent_map is False:
            self._parent_map = self.json_sidecar_settings.get('parent_map')
        return self._parent_map

    @property
    def player_drawing(self):
        if self._player_drawing is None:
            self._player_drawing = self._find_best_default('player_drawing')
        return self._player_drawing

    @property
    def xml(self):
        if self._xml is None:
            self._xml = self._generate_xml()
        return self._xml

    # Private Methods

    def _create_dir(self):
        """Create the destination dir for this map"""
        logger.debug(
            "Creating directory %s for %s", self.destination_dir, self.name
        )
        os.makedirs(self.destination_dir, exist_ok=True)

    def _find_best_default(self, key):
        """Find and return the highest priority default value for a key"""
        if self.json_sidecar_settings.get(key):
            return self.json_sidecar_settings[key]
        elif self._campaign_defaults.get(key):
            return self._campaign_defaults[key]
        else:
            return self._runtime_defaults[key]

    def _generate_updated_json(self):
        """Generates the updated JSON for this map"""
        return {
            'map_id': self.id,
            'name': self.name,
            'parent_map': self.parent_map,
            'player_drawing': self.player_drawing,
            'grid': self.grid,
            'grid_size': self.grid_size,
            'grid_offset': self.grid_offset,
            'grid_snap': self.grid_snap,
            'brush_size': self.brush_size
        }

    def _generate_layers(self):
        """Creates the Layers for this map"""
        layers = []
        layer_id = 0
        for dir_name, gridless in self._get_gridless():
            rel_path = dir_name.replace(self.source_directory, '')
            layer_name = ' - '.join(filter(None, rel_path.split(os.path.sep)))
            layers.append(
                Layer(
                    layer_name, self, layer_id,
                    os.path.join(dir_name, gridless)
                )
            )
            layer_id += 1
        for occluder in self.occluders:
            layers.append(
                Layer(
                    None, self, layer_id, occluder=occluder
                )
            )
        return layers

    def _generate_xml(self):
        """Generates the XML for this map"""
        image_id_root = ET.Element('id-{:05d}'.format(self.id))
        image_elem = ET.SubElement(image_id_root, 'image', type='image')
        subelements = [
            ('allowplayerdrawing', BOOLEAN_MAP_INVERSE[self.player_drawing]),
            ('grid', BOOLEAN_MAP_INVERSE[self.grid]),
            ('gridsize', self._to_xml_int_coord(self.grid_size)),
            ('gridoffset', self._to_xml_int_coord(self.grid_offset)),
            ('gridsnap', BOOLEAN_MAP_INVERSE[self.grid_snap]),
            ('brushsize', self._to_xml_float_coord(self.brush_size))
        ]
        for name, text in subelements:
            _ = ET.SubElement(image_elem, name)
            _.text = text
        layers = ET.SubElement(image_elem, 'layers')
        for layer in self.layers:
            # We want the layer list to be inversed
            layers.insert(0, layer.xml)
        locked = ET.SubElement(image_id_root, 'locked', type='number')
        locked.text = '0'
        name = ET.SubElement(image_id_root, 'name', type='string')
        name.text = self.name
        return image_id_root

    def _get_gridless(self):
        """Return a list of gridless files in subdirectories

        Returns:
            str, str: Returns the directory name and the filename of the
            gridless jpeg image.

        """
        found = {}
        for dir_name, _, file_list in os.walk(self.source_directory):
            for filename in file_list:
                if filename.lower().endswith('gridless.jpg'):
                    if dir_name in found:
                        raise ValueError(
                            "More than one gridless file found in directory "
                            "{dir_name}. Conflicting files:\n"
                            "{file1}\n"
                            "{file2}".format(
                                dir_name=dir_name,
                                file1=found[dir_name],
                                file2=filename
                            )
                        )
                    found[dir_name] = filename
                    yield dir_name, filename

    def _to_xml_float_coord(self, value):
        """Takes a (x, y) tuple with floats and returns a 'x,y' string."""
        float_values = (float(_) for _ in value)
        return '{:.4},{:.4}'.format(*float_values)

    def _to_xml_int_coord(self, value):
        """Takes a (x, y) tuple with ints and returns a 'x,y' string."""
        float_values = (int(_) for _ in value)
        return '{},{}'.format(*float_values)

    def _read_json_sidecar(self):
        """Read and return the contents of the JSON sidecar file for this map"""
        with open(self.json_filepath, 'r') as f:
            return json.load(f)

    def _read_occluder_xml(self):
        """Read and return the occluder XML sidecar file"""
        xml = ET.parse(self.occluder_xml_filepath)
        return list(xml.getroot())

    # Public Methods

    @classmethod
    def build_maps(cls, directory, database, respect_db=True):
        map_files = sorted(os.listdir(directory))
        for map_name in map_files:
            if os.path.isdir(os.path.join(directory, map_name)):
                try:
                    if not respect_db:
                        # Complete overwrite of DB
                        raise KeyError()
                    db_map = database.maps[map_name]
                except KeyError:
                    db_map = {
                        'name': map_name,
                    }

                cls(
                    directory=os.path.abspath(
                        os.path.join(directory, map_name)
                    ),
                    campaign_db=database,
                    **db_map
                )

    def copy_images(self, overwrite=False):
        """Copies the layer images for this map to the campaign dir"""
        self._create_dir()
        for layer in self.layers:
            if layer.source_filename:
                if overwrite or not os.path.exists(layer.destination_filename):
                    # This could be a race conditions, but that seems
                    # very unlikely to occur and the workaround is ugly.
                    logger.debug(
                        "Copying %s to %s",
                        layer.source_filename, layer.destination_filename
                    )
                    shutil.copy2(
                        layer.source_filename,
                        layer.destination_filename
                    )

    @classmethod
    def copy_maps(cls, overwrite=False):
        """Copies all map layers for all maps to the campaign dir"""
        for map in cls.maps.values():
            map.copy_images(overwrite=overwrite)

    def save_json_settings(self):
        """Saves the JSON settings to a JSON file in the base map dir."""
        logger.debug(
            "Saving JSON settings for %s to %s",
            self.name, self.json_filepath
        )
        with open(self.json_filepath, 'w') as f:
            json.dump(
                self._generate_updated_json(), f,
                indent=4, separators=(',', ': ')
            )

    def save_occluders(self):
        """Saves the occluders XML to an XML file in the base map dir."""
        if not self.occluders:
            logger.debug("No occluders to save for %s", self.name)
            return

        xml_root = ET.Element('saved-occluders')
        for occluder in self.occluders:
            xml_root.append(occluder)

        logger.debug(
            "Saving XML occluders for %s to %s",
            self.name, self.occluder_xml_filepath
        )

        with open(self.occluder_xml_filepath, 'w') as f:
            f.write(
                NEWLINE_FIX.sub(
                    '',
                    minidom.parseString(
                        ET.tostring(xml_root)
                    ).toprettyxml(indent="\t")
                )
            )

    @classmethod
    def save_all_sidecar_files(cls):
        """Saves JSON and XML sidecar files for all maps"""
        for map in cls.maps.values():
            map.save_sidecar_files()

    def save_sidecar_files(self):
        """Save JSON and XML sidecar files for this map"""
        self.save_json_settings()
        self.save_occluders()

    @classmethod
    def set_campaign_defaults(cls, campaign_dir):
        """Read the campaign default settings json."""
        json_settings = os.path.join(campaign_dir, 'settings.json')
        with open(json_settings, 'r') as f:
            logger.info("Reading campaign defaults from %s", json_settings)
            cls._campaign_defaults = json.load(f)

    @classmethod
    def set_missing_ids(cls):
        """Sets ids for all maps to unused ID values"""
        existing_ids = [_.id for _ in cls.maps.values() if _.id is not None]

        def _get_next_id():
            for i in range(len(cls.maps)):
                if i + 1 not in existing_ids:
                    yield i + 1
                else:
                    continue

        ids = _get_next_id()

        for map in cls.maps.values():
            if map.id is None:
                map_id = next(ids)
                logger.debug("Setting %s to id %s", map.name, map_id)
                map.id = map_id

    @classmethod
    def set_runtime_defaults(cls, defaults):
        """Set the default dictionary to the given values."""
        cls._runtime_defaults = defaults


class Layer(object):
    """Representing a layer of a map."""

    def __init__(self, name, map, layer_id, filename=None, occluder=None):

        logger.info("Instantiating layer %s - %s", map.name, name)

        self.name = name
        self.id = layer_id
        self.map = map
        self.source_filename = filename
        self.occluder = occluder
        self._xml = None

    # Properties

    @property
    def destination_filename(self):
        return os.path.join(
            self.map.destination_dir, '{name}{ext}'.format(
                name=self.name,
                ext=self.extension
            )
        )

    @property
    def embed_filename(self):
        """Filename suitable for embeddng in the XML"""
        return "campaign/images/{map_name}/{layer_name}{ext}".format(
            map_name=self.map.name,
            layer_name=self.name,
            ext=self.extension
        )

    @property
    def extension(self):
        return os.path.splitext(self.source_filename)[-1]

    @property
    def xml(self):
        if self._xml is None:
            self._xml = self._generate_xml()
        return self._xml

    # Private Methods

    def _generate_xml(self):
        """Generates the XML for this Layer"""
        layer = ET.Element('layer')
        if self.name:
            name = ET.SubElement(layer, 'name')
            name.text = self.name
        layer_id = ET.SubElement(layer, 'id')
        layer_id.text = str(self.id)
        layer_type = ET.SubElement(layer, 'type')
        layer_type.text = 'image'
        bitmap = ET.SubElement(layer, 'bitmap')
        if self.source_filename:
            bitmap.text = self.embed_filename
        if self.occluder:
            occluders = ET.SubElement(layer, 'occluders')
            occluders.append(self.occluder)
        return layer


# Private Functions

def _find_campaign_dir(campaign, campaign_dir=''):
    if campaign_dir and os.path.isdir(campaign_dir):
        logger.info("Using provided campaign dir of %s", campaign_dir)
        return campaign_dir
    elif not campaign_dir:
        substitutions = {
            'APPDATA': os.environ.get('APPDATA'),
            'HOME': os.environ.get('HOME'),
            'campaign': campaign
        }
        for directory in CAMPAIGN_DIRS:
            dirpath = directory.format(**substitutions)
            if os.path.isdir(dirpath):
                logger.debug("Found campaign dir at %s", dirpath)
                return dirpath
            else:
                logger.debug("No campaign dir found at %s", dirpath)
    raise ValueError(
        "No valid campaign directory found."
    )


def _parse_args():
    """Parse the arguments and return a dictionary of values"""

    parser = ArgumentParser()
    parser.add_argument(
        "map_dir",
        help="the path to the root map directory"
    )
    parser.add_argument(
        "-c",
        "--campaign",
        help="the name of the campaign for this mapping project. Defaults to "
             "base folder name of the map directory."
    )
    parser.add_argument(
        "--campaign-dir",
        help="the path to the campaign data directory. If not given, "
             "will search in typical places based on environment. This is the "
             "exact subdirectory of your campaign, where db.xml and the images "
             "folder are found."
    )
    parser.add_argument(
        "--grid-size",
        type=int,
        default=100,
        help="specify the default grid size for all maps. Defaults to 100."
    )
    parser.add_argument(
        "--grid-offset-x",
        type=int,
        default=0,
        help="specify the default grid offset x value for all maps. Defaults "
             "to 0."
    )
    parser.add_argument(
        "--grid-offset-y",
        type=int,
        default=0,
        help="specify the default grid offset y value for all maps. Defaults "
             "to 0."
    )
    parser.add_argument(
        "--brush-size",
        type=float,
        default=0,
        help="specify the default brush size for all maps. Defaults to 1/10 "
             "the grid size"
    )
    parser.add_argument(
        "--overwrite-images",
        action='store_true',
        help="overwrites all images in the campaign dir."
    )
    parser.add_argument(
        "--overwrite-db",
        action='store_true',
        help="does not archive and preserve existing map settings in the "
             "campaign. This will wipe out all grid size settings, occluders, "
             "etc. These settings will be given either the default values or "
             "the values set in the JSON and XML sidecar files. "
             "Setting this will also disable updating the JSON sidecar and "
             "XML sidecar files, as the campaign values are ignored."
    )
    parser.add_argument(
        "--disable-saving",
        action='store_true',
        help="disables the saving of map settings and occluders to the XML "
             "sidecar files. Is 'overwrite-db' is set, this is set as well."
    )
    parser.add_argument(
        "--disallow-player-drawing",
        action='store_true',
        help="turns off player drawing on all maps. Takes JSON override."
    )
    parser.add_argument(
        "--disable-grid",
        action='store_true',
        help="turns off the grid on all maps. Takes JSON override."
    )
    parser.add_argument(
        "--disable-grid-snap",
        action='store_true',
        help="turns off grid snapping on all maps. Takes JSON override."
    )

    args = parser.parse_args()

    args.map_dir = os.path.abspath(args.map_dir)

    if not args.campaign:
        args.campaign = os.path.basename(args.map_dir)

    if not args.brush_size:
        args.brush_size = args.grid_size * 0.1

    if args.overwrite_db:
        args.disable_saving = True

    return args


# Public Functions

def main():
    args = _parse_args()

    logging.basicConfig()
    logger.setLevel(logging.DEBUG)

    # Find the campaign dir
    try:
        campaign_dir = _find_campaign_dir(
            args.campaign, vars(args).get('data-dir', '')
        )
    except ValueError:
        raise RuntimeError(
            "No valid campaign directory found. Check that your campaign name "
            "is correct, or specify an exact directory with --campaign-dir. If "
            "you have not pre-created the campaign directory, please do that "
            "first."
        )

    # Build Campaign DB object
    try:
        db = CampaignDB(campaign_dir)
    except ValueError:
        raise RuntimeError(
            "No db.xml file found in the campaign directory. Please check "
            "campaign directory."
        )

    # Set campaign default values
    try:
        Map.set_campaign_defaults(campaign_dir)
    except (FileNotFoundError, JSONDecodeError):
        logger.info("No campaign default settings found")
        pass

    # Set default fallback values
    Map.set_runtime_defaults(
        {
            'player_drawing': not args.disallow_player_drawing,
            'grid': not args.disable_grid,
            'grid_size': (args.grid_size, args.grid_size),
            'grid_offset': (args.grid_offset_x, args.grid_offset_y),
            'grid_snap': not args.disable_grid_snap,
            'brush_size': (args.brush_size, args.brush_size)
        }
    )

    # Build maps based on jpegs
    Map.build_maps(args.map_dir, db, respect_db=not args.overwrite_db)
    Map.set_missing_ids()

    # copy images to campaign folder
    Map.copy_maps(overwrite=args.overwrite_images)

    if not args.disable_saving:
        # save sidecar files
        Map.save_all_sidecar_files()

    # Update DB
    db.update_db(Map.maps.values())

    # Save DB
    db.save_db()


# Main

if __name__ == '__main__':
    main()
