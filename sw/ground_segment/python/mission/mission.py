#!/usr/bin/env python

from __future__ import print_function

import sys
from os import path, getenv
import numpy as np
import shapely.geometry as geometry
import math
import time

# if PAPARAZZI_SRC or PAPARAZZI_HOME not set, then assume the tree containing this
# file is a reasonable substitute
PPRZ_HOME = getenv("PAPARAZZI_HOME", path.normpath(path.join(path.dirname(path.abspath(__file__)), '../../../../')))
PPRZ_SRC = getenv("PAPARAZZI_SRC", path.normpath(path.join(path.dirname(path.abspath(__file__)), '../../../../')))
sys.path.append(PPRZ_SRC + "/sw/lib/python")
sys.path.append(PPRZ_HOME + "/var/lib/python") # pprzlink

from pprzlink.ivy import IvyMessagesInterface
from pprzlink.message import PprzMessage
from pprz_math import geodetic

import static_nfz
import coordinate_transformations as coord_trans
import asterix_receiver
import asterix_visualizer
import aircraft
import resolution

import flightplan_xml_parse

#defines
static_margin = 100. #[m]
altitude = 120.
airspeed = 23.

class Mission(object):
    def __init__(self, ac_id):
        # Load flightplan
        self.ac_id = ac_id
        self.ivy_interface = IvyMessagesInterface("Avoid Mission")
        self.flightplan = flightplan_xml_parse.PaparazziACFlightplan(self.ac_id)
        
        # Reference values for locations
        self.ref_lla_i = geodetic.LlaCoor_i(int(self.flightplan.flight_plan.lat0*10.**7), int(self.flightplan.flight_plan.lon0*10.**7), int(0.*1000.))
        self.ref_utm_i = geodetic.UtmCoor_i()
        geodetic.utm_of_lla_i(self.ref_utm_i, self.ref_lla_i)  
        self.ltp_def = geodetic.LlaCoor_f(self.flightplan.flight_plan.lat0/180*math.pi, self.flightplan.flight_plan.lon0/180*math.pi, self.flightplan.flight_plan.alt).to_ltp_def()   
        
        # From flightplan
        self.transit_points = self.transit_waypoints_from_fp()
        self.static_nfzs = self.static_nfzs_from_fp()
        self.geofence = self.geofence_from_fp()
        self.circular_zones = static_nfz.get_circular_zones(self.static_nfzs)
        
        # mission elements
        self.altitude = altitude
        self.mission_elements = []
        
        # asterix
        self.asterixreceiver = asterix_receiver.AsterixReceiver()
        self.asterixvisualizer = asterix_visualizer.AsterixVisualizer(self.ivy_interface)
        
        # own aircraft
        self.aircraft = aircraft.Aircraft(self.ac_id, self.ivy_interface)
        
        # realtime ssd
        self.realtime_ssd = resolution.RealtimeResolution(self.circular_zones)
        
        
    def static_nfzs_from_fp(self):
        """
        Get the static no fly zones from the flight plan
        """
        zones = []
        for sector in self.flightplan.sectors.member_list:
            if "NFZ" in sector.name:
                enu_points = []
                for point in sector.corner_list:
                    point_enu = geodetic.LlaCoor_f(point.lat/180*math.pi, point.lon/180*math.pi, 0).to_enu(self.ltp_def)
                    enu_points.append(point_enu)
                zones.append(StaticNFZ(sector.name, enu_points))
        return zones
        
    def transit_waypoints_from_fp(self):    
        """
        Get the transit waypoints from the flight plan
        """
        waypoints = []
        for wp in self.flightplan.waypoints.member_list:
            # Select only waypoints from the Main path
            if "WP" in wp.name:
                wp_enu = geodetic.LlaCoor_f(wp.lat/180*math.pi, wp.lon/180*math.pi, self.flightplan.flight_plan.alt).to_enu(self.ltp_def)
                wp = TransitWaypoint(wp.name, wp_enu)
                waypoints.append(wp)
        return waypoints    
        
    def geofence_from_fp(self):
        """
        Get the geofence from the flight plan
        """
        enu_points = []
        for point in self.flightplan.sector_name_lookup['SoftBoundary'].corner_list:
            point_enu = geodetic.LlaCoor_f(point.lat/180*math.pi, point.lon/180*math.pi, 0).to_enu(self.ltp_def)
            enu_points.append(point_enu)
        return enu_points
        
    def draw_circular_static_nfzs(self):
        """
        Draw a cirular NFZ
        """
        id = 0
        for zone in self.circular_zones:
            enu_coor = geodetic.EnuCoor_f(zone[0], zone[1], 0)
            lla_coor = enu_coor.to_ecef(self.ltp_def).to_lla().to_int()
            
            msg = PprzMessage("ground", "SHAPE")
            msg['id'] = 0 + id 
            msg['linecolor'] = "red"
            msg['fillcolor'] = "orange"
            msg['opacity'] = 1
            msg['shape'] = 0 # Circle
            msg['status'] = 0 # Create
            msg['latarr'] = [lla_coor.lat, 0] # e-7 deg
            msg['lonarr'] = [lla_coor.lon, 0] # e-7 deg
            msg['radius'] = zone[2]
            self.ivy_interface.send(msg)
            id += 1
            
    def initialize_mission_elements(self):
        """
        Add the mission flight plan
        """
        id = 0
        for point in self.transit_points:
            elem = MissionElement(id, point.enu)
            self.mission_elements.append(elem)
            id += 100

    def run_mission(self):
        """
        Main loop
        """
        self.initialize_mission_elements()
        self.asterixreceiver.start()
        
        while True:
            self.draw_circular_static_nfzs()
            self.asterixvisualizer.visualize(self.asterixreceiver.get_events())
            time.sleep(1)

# Mission element
class MissionElement(object):
    def __init__(self, id, wp):
        self.wp = geodetic.EnuCoor_f()
        self.wp_id = 0

# Transit waypoint
class TransitWaypoint(object):
    def __init__(self, name, enu):
        self.name = name
        self.enu = enu

# Static no fly zone  
class StaticNFZ(object):
    def __init__(self, name, enu_points):
        self.name = name
        self.enu_points = enu_points
        
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="Mission for Avoidance")
    parser.add_argument("-ac", "--ac_id", dest='ac_id', default=0, type=int, help="aircraft ID")
    args = parser.parse_args()
    
    ac_id = args.ac_id

    mission = Mission(ac_id)
    mission.run_mission()