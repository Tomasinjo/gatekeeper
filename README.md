# Gatekeeper
Enables selective access from the internet to your self hosted services by whitelisting public IPs that sucessfully authenticated to selected service.

# Motivation
I host multiple services for personal use, and of course want to use them also outside my own LAN. However, I don't like traditional approaches like always-on VPN or external SSO providers.

So I decided to expose reverse proxy to the internet and use client certificates for all services, but it turns out that not all clients support it, e.g. NextCloud for Android. The service that actually works good with mTLS is Home Assistant. 

But then I got the idea: if a client successfully authenticates to Home Assistant using client cert, then I can treat it's IP as trustworthy and allow it to access also all other services that I otherwise wouldn't risk exposing to the internet.

I find this approach a great balance between security and usability, however it is worth noting that this is not replacement for authentication, 2FA, hardening and other basic security measures. Keep in mind that whitelisted public IP can also be used by hundreds of other clients behind NAT for example. This can be partially mitigated by setting maximum number of whitelisted IPs to a small number.

# How it works
Traefik is set up to mirror all requests destined to selected service also to another destination - the gatekeeper. Traefik will only mirror client requests after the client certificate was verified. The app will catch all requests of trusted client and whitelist its public IP address. This client can now access all other services. Number of whitelisted IPs is limited, oldest will be removed if the limit is hit.

# Prerequesites
- Have at least one service that allows you to authenticate using client certificate from your phone and periodically talks to it in the background. Home Assistant is perfect because it is updating sensors every 15 minutes (or even 1 minute if set so). This ensures the whitelist is always up to date.
- Traefik, because it supports dynamic loading of configuration changes.
- Other services, that might not support client certificates and you don't want to expose to the whole internet.

# Installation
#### 1. Configure file provider, if not already

`traefik.yml`:
```yml
providers:  
  file:  
    directory: /etc/traefik/file_providers  
    watch: true  
```
The directory defined above is used inside a container, but I mount it to `./traefik/etc/file_providers` on host.

#### 2. Create a whitelist file dynamic-whitelist.yml
Should be wherever you mapped Traefik's config on host, in the file provider directory.  
`touch ./traefik/etc/file_providers/dynamic-whitelist.yml`

#### 3. Add the gatekeeper to your docker-compose.yml. 
Mount `dynamic-whitelist.yml` to previously touched whitelist file on host. 

Add your prefered settings to environmental variables:  
`MAX_IP_LEN` - maximum number of unique whitelisted IPs at the same time, oldest IP will get removed first.  
`DEFAULT_SOURCE_RANGE` - any IPs that you want to persist in whitelist. They do not count against `MAX_IP_LEN`.  
```yml
  gatekeeper:
    image: ghcr.io/tomasinjo/gatekeeper:main
    container_name: gatekeeper
    restart: unless-stopped
    volumes: 
      - ./traefik/etc/file_providers/dynamic-whitelist.yml:/app/dynamic-whitelist.yml
    expose: 
      - 5000
    environment:
      - MAX_IP_LEN=4
      - DEFAULT_SOURCE_RANGE=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
```

#### 4. Configure traefik to mirror all requests from trusted service to this app
A Traefik file provider must be used. Create a file `./traefik/etc/file_providers/http.yml`.  

The following will mirror each request to trusted service also to gatekeeper which will whitelist the request's public IP. Remember that the mirroring happens after the client provided the certificate.
```yml
http:
  services:
    ha-mirror:
      mirroring:
        service: ha
        mirrors:
        - name: gatekeeper
          percent: 100
    ha:
      loadBalancer:
        servers: 
        - url: http://ha:8123 # <- this is your trusted service with mTLS configured.
    gatekeeper:
      loadBalancer:
        servers: 
        - url: http://gatekeeper:5000/
```
Reference the service in docker-compose labels so the traefik will use it:
```yml
services:
  ha:
    container_name: ha
    image: "ghcr.io/home-assistant/home-assistant:stable"
    # < redacted >
    labels:
      - traefik.enable=true
      - traefik.http.routers.ha.rule=Host(`trusted-service.your-domain.com`)
      - traefik.http.routers.ha.entrypoints=https,http
      - traefik.http.routers.ha.service=ha-mirror@file  # <- service defined above
      - traefik.http.routers.ha.tls=true
      - traefik.http.routers.ha.tls.certresolver=your_resolver
      - traefik.http.routers.ha.tls.options=your_mtls@file # client cert auth
```
#### 5. Configure your other services to only allow whitelisted IPs

Example:
```yml
services:
  immich-server:
    container_name: immich_server
    image: ghcr.io/immich-app/immich-server:${IMMICH_VERSION:-release}
    # < redacted >
    labels:
      - traefik.enable=true
      - traefik.http.routers.immich.rule=Host(`hidden-service.your-domain.com`)
      - traefik.http.routers.immich.entrypoints=https,http
      - traefik.http.routers.immich.tls=true
      - traefik.http.routers.immich.middlewares=dynamic-whitelist@file # <-- add this
      - traefik.http.routers.immich.tls.certresolver=your_resolver
```
#### 6. Rebuild modified containers, restart traefik and test it

On your phone, use mobile network to connect to your trusted service. Then check out new contents of dynamic-whitelist.yml. Your phone's IP should be there and you should be able to access your other services.

Troubleshoot the gatekeeper using: `docker logs gatekeeper` 