# Gatekeeper
Enables selective access from the internet to your self hosted services by whitelisting public IPs that sucessfully authenticated to selected service.

# Motivation
I host multiple services for personal use, and of course want to use them also outside my own LAN. However, I don't like traditional approaches like always-on VPN or external SSO providers.

So I decided to expose reverse proxy to the internet and use client certificates for all services, but it turns out that not all clients support it, e.g. NextCloud for Android. The service that actually works good with mTLS is Home Assistant. 

But then I got the idea: if a client successfully authenticates to Home Assistant using client cert, then I can treat it's IP as trustworthy and allow it to access also all other services that I otherwise wouldn't risk exposing to the internet.

I find this approach a great balance between security and usability, however it is worth noting that this is not replacement for authentication, 2FA, hardening and other basic security measures. Keep in mind that whitelisted public IP can also be used by hundreds of other clients behind NAT for example. This can be partially mitigated by setting maximum number of whitelisted IPs to a small number.

# How it works
Traefik is set up to mirror all requests destined to selected service also to another destination - the gatekeeper. Traefik will only mirror client requests after the client certificate was verified. The app will catch all requests of trusted client and whitelist its public IP address. This client can now access all other services. Number of whitelisted IPs is limited, oldest will be removed if the limit is hit.

** **Update 11.2025: Shared links (tested on Immich and Nextcloud) are now supported. See [here](#allow-ip-using-shared-links)**

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


## Allow IP using shared links

While gatekeeper works great for small amount of users, you might still want certain people to access files shared via Immich or Nextcloud. The following configuration will whitelist IP of device that opens a shared link. The examples are for Immich and Nextcloud, but should work with any other service that generates a random unique URI for shares and returns non-200 response (e.g. 404) if link doesn't exist.

### How it works
When your friend opens a shared URL with some immich photos, the request will be matched and forwarded to gatekeeper via [forwardauth middleware](https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/forwardauth/). This middleware pauses the your friend's request and waits for gatekeeper to verify validity of the shared link. If it is valid (immich server responds with HTTP code 200), your friend's IP is whitelisted and the URL with photos will load. 


### Example for Immich and Nextcloud

#### 1. Add Traefik labels to gatekeeper to enable separate forwardauth middleware for both Immich and Nextcloud. 
```yml
  gatekeeper:
    .
    . < omitted for clarity, see compose example from above>
    .
    labels:
      - "traefik.enable=true"
      - "traefik.http.middlewares.gatekeeper_immich_share.forwardauth.address=http://gatekeeper:5000/verify_share_request?protocol=http&container_name_port=immich_server:2283"
      - "traefik.http.middlewares.gatekeeper_immich_share.forwardauth.trustForwardHeader=true"

      - "traefik.http.middlewares.gatekeeper_nextcloud_share.forwardauth.address=http://gatekeeper:5000/verify_share_request?protocol=http&container_name_port=nextcloud-app:80"
      - "traefik.http.middlewares.gatekeeper_nextcloud_share.forwardauth.trustForwardHeader=true"
```

**Explaination**  
traefik.http.middlewares.**MIDDLEWARE_NAME**.forwardauth.address=http://gatekeeper:5000/verify_share_request?protocol=**PROTOCOL**&container_name_port=**CONTAINER_NAME_AND_PORT**

MIDDLEWARE_NAME: Just a middleware name, will be refered later in specific service  
PROTOCOL: http or https. Used by container, not reverse proxy!  
CONTAINER_NAME_AND_PORT: Used by container, not reverse proxy! See examples above for immich and nextcloud. Your containers might not use the same name.
______
  
#### 2. Modify Immich and Nextcloud to forward share requests to gatekeeper using forwardauth

Add the following labels to Immich compose (but keep the ones added during the [installation step](#installation)!):
```yml
- traefik.http.routers.immich-share.rule=Host(`immich.domain.com`) && PathRegexp(`^\/share\/(?:[A-Z,a-z,0-9,_,-]){67}$`)
- traefik.http.routers.immich-share.entrypoints=https,http
- traefik.http.routers.immich-share.tls=true
- traefik.http.routers.immich-share.tls.certresolver=your_resolver
- traefik.http.routers.immich-share.middlewares=gatekeeper_immich_share@docker
```
This tells Traefik to match reqests to shared links and forward them to gatekeeper before allowing user to access them. Notice there is no whitelist restrictions, anyone can access a URL that matches regex.

Example immich share link. Regex at first line above matches this pattern (67 characters, mixed alphanumeric including _ and -):  
```immich.domain.com/share/TEsm6bGu0tN5o1PjeVHDY0vwIqIfNDVPbVSzyfgI_jif2h0r8_GogiXmz8g3ziKYBh4```

Last line refers to middleware name defined in gatekeeper's labels. Other lines configure TLS and can be copied from existing labels.
  
_____________  
Nexcloud labels, regex is different and of course the middleware name:
```yml
- traefik.http.routers.nextcloud-share.rule=Host(`nextcloud.yourdomain.com`) && PathRegexp(`^\/s\/(?:[A-Z,a-z,0-9]){15}$`)
- traefik.http.routers.nextcloud-share.entrypoints=https,http
- traefik.http.routers.nextcloud-share.tls=true
- traefik.http.routers.nextcloud-share.tls.certresolver=your_resolver
- traefik.http.routers.nextcloud-share.middlewares=gatekeeper_nextcloud_share@docker
```

#### 3. Bring up your stacks to apply new labels


### Security
When someone access a valid shared link, they will be granted access also to any other exposed service because their IP is added to the whitelist. I might consider adding dedicated whitelists for the specific services. However, since I typically share stuff with people I trust, I don't see it as a huge risk (keep in mind they would still need password + 2FA to actually access anything).

Another thing to consider is bruteforcing shared links. If someone would guess a valid shared link, the gatekeeper will whitelist them and give them the connectivity to your services. Immich has long shared links, but Nextcloud, for example, has only 15 characters. Still OK, especially because Nextcloud will throttle aggressive requests. 

Easy way of reducing chances of successful bruteforce is to limit validity of shared links to short periods.
