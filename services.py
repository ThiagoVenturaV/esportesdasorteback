"""
Root aggregator for all backend modules. 
Main.py imports from here to maintain a clean <80-line app instance.
"""
from models import *
from auth.service import *
from core.utils import *
from analysis.service import *
from chat.service import *
from odds.service import *
from live.service import *
