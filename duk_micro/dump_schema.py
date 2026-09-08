import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_mock_engine
from sqlalchemy.schema import CreateTable

import importlib.util

def load_base(service_name):
    path = os.path.join(os.path.dirname(__file__), "services", service_name, "models_local.py")
    spec = importlib.util.spec_from_file_location(f"{service_name}_models", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Base

def dump_sql():
    AuthBase = load_base("auth-service")
    AdminBase = load_base("admin-service")
    NotifBase = load_base("notification-api")
    
    bases = [AuthBase, AdminBase, NotifBase]
    
    def dump(sql, *multiparams, **params):
        print(sql.compile(dialect=engine.dialect).string + ";\n")
        
    engine = create_mock_engine('postgresql://', executor=dump)
    
    for Base in bases:
        Base.metadata.create_all(engine, checkfirst=False)

if __name__ == "__main__":
    dump_sql()
