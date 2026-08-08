from datetime import datetime
import os
from pathlib import Path
import uuid
import pytest
# import dotenv
from sqlmodel import  Field, SQLModel, Session, create_engine, delete

from edutap.wallet_apple import models
from edutap.wallet_apple_vas_web_service import db_models

# dotenv.load_dotenv()  # we need an environment var "CLASS_ID" for ticket creation

cwd = Path(__file__).parent
data = cwd / "data"
jsons = data / "jsons"
resources = data / "resources"
generated_passes_dir = data / "generated_passes"
certs = data / "certs"
password_file = certs / "password.txt"
cert_file = certs / "certificate.pem"
key_file = certs / "private.key"
wwdr_file = certs / "wwdr_certificate.pem"

uri = "postgresql://phil:uehagrawupl@localhost:5432/edutaptest"

@pytest.fixture
def engine():
    res = create_engine(uri)
    db_models.init_model(res)
    return res

@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session
        # session.rollback()
        session.commit()
        
@pytest.fixture
def new_session(engine):
    delete_all(engine)
    with Session(engine) as session:
        
        yield session
        # session.rollback()
        session.commit()    

def delete_all(engine):
    with Session(engine) as session:
        session.exec(delete(db_models.ApplePassRegistry))
        session.exec(delete(db_models.ApplePassData))
        session.exec(delete(db_models.AppleDeviceRegistry))
        session.commit()



def test_create_database(engine):
    delete_all(engine)
        
        

def load_event_pass():
    buf = open(jsons / "event_ticket.json").read()
    pass1 = models.Pass.model_validate_json(buf)

    assert pass1.eventTicket is not None
    assert pass1.passInformation.__class__ == models.EventTicket
    json_ = pass1.model_dump(exclude_none=True)
    assert json_
    pass1.addFile("icon.png", open(resources / "white_square.png", "rb"))
    pass1.addFile("strip.png", open(resources / "eaie-hero.jpg", "rb"))

    passdata: db_models.ApplePassData = db_models.ApplePassData.from_pass(pass1)
    
    return passdata
 
 
def create_event_pass():
    pass_file_name = generated_passes_dir / "eventticket.pkpass"

    cardInfo = models.EventTicket()
    cardInfo.addPrimaryField("title", "EAIE2023", "event")
    stdBarcode = models.Barcode(
        message="test barcode", format=models.BarcodeFormat.CODE128, altText="alternate text"
    )
    sn = uuid.uuid4().hex
    passfile = models.Pass(
        eventTicket=cardInfo,
        organizationName="eduTAP",
        passTypeIdentifier="pass.demo.lmu.de",
        teamIdentifier="JG943677ZY",
        serialNumber=sn,
        description="edutap Sample Pass",
        webServiceURL="https://edutap.bluedynamics.net:8443/apple_update_service/v1",
        authenticationToken="0123456789012345"  # must be 16 characters
    )

    passfile.barcode = stdBarcode

    passfile.addFile("icon.png", open(resources / "edutap.png", "rb"))
    passfile.addFile("iconx2.png", open(resources / "edutap.png", "rb"))
    passfile.addFile("logo.png", open(resources / "edutap.png", "rb"))
    passfile.addFile("logox2.png", open(resources / "edutap.png", "rb"))
    passfile.addFile("strip.png", open(resources / "eaie-hero.jpg", "rb"))
    # passfile.addFile("background.png", open(resources / "eaie-hero.jpg", "rb"))
    passfile.backgroundColor = "#fa511e"

    return passfile

def test_load_event_pass_export():
    """
    create a pass, create a ApplePassData instance, export it and open it
    on OSX the pass should be dsplayed in the pass viewer"""
    
    pass_file_name = generated_passes_dir / "eventticket-roundtrip.pkpass"

    pass1 = create_event_pass()
    passdata: db_models.ApplePassData = db_models.ApplePassData.from_pass(pass1)
    pass2 = passdata.to_pass()
    
    zip = pass2.create(
        certs / "private" / "certificate.pem",
        certs / "private" / "private.key",
        certs / "private" / "wwdr_certificate.pem",
        "",
    )
    
    open(pass_file_name, "wb").write(zip.getvalue())
 
    os.system("open " + str(pass_file_name))
    
    
@pytest.mark.integraton
def test_load_event_pass(new_session: Session):
    """
    same as above but with database interaction
    """
    pass_file_name = generated_passes_dir / "eventticket-roundtrip.pkpass"

    pass1 = create_event_pass()
    passdata: db_models.ApplePassData = db_models.ApplePassData.from_pass(pass1)
    new_session.add(passdata)
    new_session.commit()
    
    passdata = new_session.query(db_models.ApplePassData).get((passdata.passTypeIdentifier, passdata.serialNumber))
    
    pass2 = passdata.to_pass()
    zip = pass2.create(
        certs / "private" / "certificate.pem",
        certs / "private" / "private.key",
        certs / "private" / "wwdr_certificate.pem",
        "",
    )
    
    open(pass_file_name, "wb").write(zip.getvalue())
 
    os.system("open " + str(pass_file_name))
 